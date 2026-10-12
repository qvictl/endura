"""Default options. Override any key in /etc/endura/config.json under "options"."""

OPTIONS = {
    "battery_epp": "balance_performance",
    "battery_boost": False,
    "abm_level": 1,
    # timer slack: /proc/<pid>/comm names (max 15 chars)
    "slack_deep": ["sunloginclient", "oray_rundaemon", "ToDesk_Service", "ToDesk",
                   "teamviewerd", "anydesk", "rustdesk"],
    "slack_light": ["xray", "v2ray", "sing-box", "clash", "mihomo", "kdeconnectd", "syncthing",
                    "nextcloud", "dropbox"],
    "slack_deep_ns": 500_000_000,
    "slack_light_ns": 50_000_000,
    # App Nap: cgroup leaf globs under the user's app.slice.
    # tier 1 naps when unfocused; tier 2 only after 5 minutes without input.
    "nap_apps": {
        "cn.feishu.Feishu": {"globs": ["app-flatpak-cn.feishu.Feishu-*.scope", "app-bytedance\\x2dfeishu*"],
                             "cls": ["feishu", "lark", "bytedance"], "tier": 1},
        "com.larksuite.Lark": {"globs": ["app-flatpak-com.larksuite*.scope"], "cls": ["lark"], "tier": 1},
        "com.tencent.WeChat": {"globs": ["app-flatpak-com.tencent.WeChat-*.scope", "app-wechat@*.service", "app-wechat-[0-9]*.scope"],
                               "cls": ["wechat"], "tier": 1},
        "com.slack.Slack": {"globs": ["app-flatpak-com.slack.Slack-*.scope", "app-slack@*.service", "app-slack-[0-9]*.scope"],
                            "cls": ["slack"], "tier": 1},
        "org.telegram.desktop": {"globs": ["app-flatpak-org.telegram.desktop-*.scope", "app-org.telegram.desktop@*.service", "app-org.telegram.desktop-[0-9]*.scope"],
                                 "cls": ["telegram"], "tier": 1},
        "com.discordapp.Discord": {"globs": ["app-flatpak-com.discordapp.Discord-*.scope", "app-discord@*.service", "app-discord-[0-9]*.scope"],
                                   "cls": ["discord"], "tier": 2},
        "google-chrome": {"globs": ["app-google\\x2dchrome@*.service", "app-com.google.Chrome-*.scope",
                                    "app-flatpak-com.google.Chrome-*.scope"],
                          "cls": ["google-chrome", "chrome"], "binary": "chrome", "tier": 2},
        "chromium": {"globs": ["app-chromium@*.service", "app-chromium-[0-9]*.scope", "app-chromium\\x2dbrowser*", "app-flatpak-org.chromium.Chromium-*.scope"],
                     "cls": ["chromium"], "binary": "chromium", "tier": 2},
        "firefox": {"globs": ["app-firefox@*.service", "app-firefox-[0-9]*.scope", "app-org.mozilla.firefox*", "app-flatpak-org.mozilla.firefox-*.scope"],
                    "cls": ["firefox"], "binary": "firefox", "tier": 2},
    },
}


def load_options(path="/etc/endura/config.json"):
    import json
    opts = {k: (dict(v) if isinstance(v, dict) else v) for k, v in OPTIONS.items()}
    try:
        user = json.load(open(path)).get("options", {})
    except (OSError, ValueError):
        user = {}
    opts.update(user)
    return opts
