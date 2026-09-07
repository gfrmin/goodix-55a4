// Offline validation: does the vendored SIGFM matcher separate our M2 corpus?
// Mirrors research/sift_match.py preprocessing (clear-finger -> norm -> x4 -> CLAHE),
// then scores with SIGFM (sigfm_match_score) instead of our RANSAC inliers. The point
// is to confirm the matcher we will SHIP (SIGFM) separates fingers on OUR own data and
// to read off a starting threshold -- no hardware, uses existing vault frames.
//
// Build: bash research/sigfm_c/build.sh    Run: research/sigfm_c/harness [frames_dir]
#include "sigfm.hpp"
#include <opencv2/opencv.hpp>
#include <dirent.h>
#include <sys/stat.h>
#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <string>
#include <vector>

// Minimal P2 (ASCII) PGM reader; values may exceed 255 (our frames are ~12-bit).
static cv::Mat read_p2(const std::string& path) {
    FILE* f = fopen(path.c_str(), "r");
    if (!f) return {};
    char magic[3] = {0};
    if (fscanf(f, "%2s", magic) != 1 || std::string(magic) != "P2") { fclose(f); return {}; }
    int w, h, maxv;
    if (fscanf(f, "%d %d %d", &w, &h, &maxv) != 3) { fclose(f); return {}; }
    cv::Mat m(h, w, CV_32S);
    for (int i = 0; i < w * h; ++i) {
        int v = 0; if (fscanf(f, "%d", &v) != 1) { fclose(f); return {}; }
        m.at<int>(i / w, i % w) = v;
    }
    fclose(f);
    return m;
}

static bool exists(const std::string& p) { struct stat st; return stat(p.c_str(), &st) == 0; }

// percentile 2/98 normalize to uint8 (matches research/nbis_test.norm8)
static cv::Mat norm8(const cv::Mat& src32) {
    std::vector<int> vals(src32.total());
    for (int i = 0; i < (int)src32.total(); ++i) vals[i] = ((int*)src32.data)[i];
    std::sort(vals.begin(), vals.end());
    double lo = vals[(int)(0.02 * vals.size())], hi = vals[(int)(0.98 * vals.size())];
    cv::Mat out(src32.size(), CV_8U);
    for (int i = 0; i < (int)src32.total(); ++i) {
        double v = (((int*)src32.data)[i] - lo) / (hi - lo + 1e-9);
        v = std::min(1.0, std::max(0.0, v));
        out.data[i] = (unsigned char)(v * 255.0);
    }
    return out;
}

// clear-finger baseline-subtracted, normalized, x4, CLAHE -- like sift_match.preprocess
static cv::Mat preprocess(const std::string& sess) {
    std::string fpn = exists(sess + "/fingerprint.pgm") ? "/fingerprint.pgm" : "/fingerprint-0.pgm";
    cv::Mat fp = read_p2(sess + fpn);
    cv::Mat c0 = read_p2(sess + "/clear-0.pgm");
    if (fp.empty() || c0.empty()) return {};
    cv::Mat diff = c0 - fp;            // CV_32S, may be negative
    cv::Mat n = norm8(diff);
    cv::Mat up; cv::resize(n, up, cv::Size(), 4, 4, cv::INTER_CUBIC);
    auto clahe = cv::createCLAHE(2.0, cv::Size(8, 8));
    cv::Mat out; clahe->apply(up, out);
    return out;
}

// Vault root, mirroring research/vault.py: $GOODIX_VAULT, else <repo>/.vault-path
// (gitignored), else ~/.local/share/goodix-55a4. Refuses a vault on the same device
// as "/": the vault is expected on removable storage, and when that is unmounted its
// mountpoint is an empty dir on the internal disk, so we would silently read/write
// biometric data there (see vault.py for the full story).
static std::string read_vault_path_file() {
    // <repo>/.vault-path, relative to this source file's location at build time.
    std::string f = std::string(SRC_DIR) + "/../../.vault-path";
    FILE* fh = fopen(f.c_str(), "r");
    if (!fh) return "";
    char buf[4096] = {0};
    std::string out;
    while (fgets(buf, sizeof buf, fh)) {
        std::string line(buf);
        if (auto h = line.find('#'); h != std::string::npos) line = line.substr(0, h);
        while (!line.empty() && isspace((unsigned char)line.back())) line.pop_back();
        size_t b = line.find_first_not_of(" \t");
        if (b == std::string::npos) continue;
        out = line.substr(b);
        break;
    }
    fclose(fh);
    return out;
}

static std::string vault_frames() {
    const char* home = getenv("HOME");
    std::string root;
    if (const char* env = getenv("GOODIX_VAULT")) {
        root = env;
    } else if (std::string f = read_vault_path_file(); !f.empty()) {
        root = f;
    } else {
        if (!home) { fprintf(stderr, "vault: HOME unset\n"); exit(1); }
        root = std::string(home) + "/.local/share/goodix-55a4";
    }
    if (!root.empty() && root[0] == '~' && home) root = std::string(home) + root.substr(1);
    if (!getenv("GOODIX_VAULT_ALLOW_ROOT")) {
        struct stat sv, sr;
        if (stat(root.c_str(), &sv) != 0 || stat("/", &sr) != 0 || sv.st_dev == sr.st_dev) {
            fprintf(stderr,
                    "refusing to use vault: %s\n"
                    "It is missing or on the root filesystem -- mount the data volume, set "
                    "GOODIX_VAULT, or write the path into .vault-path "
                    "(GOODIX_VAULT_ALLOW_ROOT=1 overrides).\n",
                    root.c_str());
            exit(1);
        }
    }
    return root + "/frames";
}

static std::string role(const std::string& name) {
    if (name.find("imp") != std::string::npos) return "impostor";
    if (name.find("prb") != std::string::npos) return "probe";
    return "gallery";
}

int main(int argc, char** argv) {
    std::string root = (argc > 1) ? argv[1] : vault_frames();
    std::vector<std::string> sessions;
    if (DIR* d = opendir(root.c_str())) {
        for (dirent* e; (e = readdir(d));) {
            std::string n = e->d_name;
            if (n.rfind("m2c-", 0) == 0) sessions.push_back(root + "/" + n);
        }
        closedir(d);
    }
    std::sort(sessions.begin(), sessions.end());
    std::map<std::string, SigfmImgInfo*> feats;
    std::map<std::string, std::string> roles;
    for (auto& s : sessions) {
        std::string name = s.substr(s.find_last_of('/') + 1);
        cv::Mat img = preprocess(s);
        if (img.empty()) { printf("SKIP %-18s (no frames)\n", name.c_str()); continue; }
        SigfmImgInfo* info = sigfm_extract(img.data, img.cols, img.rows);
        if (!info || sigfm_keypoints_count(info) == 0) { printf("SKIP %-18s (no keypoints)\n", name.c_str()); continue; }
        feats[name] = info; roles[name] = role(name);
        printf("%-9s %-18s kp=%d\n", roles[name].c_str(), name.c_str(), sigfm_keypoints_count(info));
    }
    std::vector<std::string> gal, prb, imp;
    for (auto& kv : feats) (roles[kv.first]=="gallery"?gal:roles[kv.first]=="probe"?prb:imp).push_back(kv.first);
    printf("\ngallery=%zu probes=%zu impostors=%zu (score = SIGFM match vs best gallery frame)\n",
           gal.size(), prb.size(), imp.size());
    if (gal.empty() || prb.empty() || imp.empty()) {
        printf("need gallery+probe+impostor; corpus incomplete.\n");
        return 1;
    }
    auto best = [&](const std::string& n) {
        int b = 0; for (auto& g : gal) b = std::max(b, sigfm_match_score(feats[n], feats[g])); return b;
    };
    std::vector<int> gscore, iscore;
    printf("genuine probes:\n");  for (auto& n : prb) { int s=best(n); gscore.push_back(s); printf("  %-18s -> %d\n", n.c_str(), s); }
    printf("impostor probes:\n"); for (auto& n : imp) { int s=best(n); iscore.push_back(s); printf("  %-18s -> %d\n", n.c_str(), s); }
    int hi = 1;
    for (int s : gscore) hi = std::max(hi, s);
    for (int s : iscore) hi = std::max(hi, s);
    printf("\nthreshold sweep (accept if score >= T):\n   T    FRR    FAR\n");
    int far0 = -1;
    for (int T = 0; T <= hi + 1; ++T) {
        double frr = (double)std::count_if(gscore.begin(),gscore.end(),[&](int x){return x<T;})/gscore.size();
        double far = (double)std::count_if(iscore.begin(),iscore.end(),[&](int x){return x>=T;})/iscore.size();
        if (far == 0 && far0 < 0) far0 = T;
        printf("  %2d  %5.0f%% %5.0f%%%s\n", T, frr*100, far*100, (far0==T?"  <- FAR=0":""));
    }
    int gmax = gscore.empty()?0:*std::max_element(gscore.begin(),gscore.end());
    int imax = iscore.empty()?0:*std::max_element(iscore.begin(),iscore.end());
    bool ok = (far0 >= 0) && (gmax >= far0);
    printf("\nVERDICT: %s. genuine_max=%d impostor_max=%d  FAR=0 at T=%d\n",
           ok ? "SIGFM SEPARATES our corpus" : "REVISIT (no clean threshold)", gmax, imax, far0);
    for (auto& kv : feats) sigfm_free_info(kv.second);
    return ok ? 0 : 2;
}
