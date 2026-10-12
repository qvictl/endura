// Re-add widgets from SNAPSHOT (injected by caller) and restore their order.
var snap = __ENDURA_SNAPSHOT__;
function load(w, path, o) {
    w.currentConfigGroup = path;
    for (var k in o.keys) w.writeConfig(k, o.keys[k]);
    for (var g in o.groups) load(w, path.concat([g]), o.groups[g]);
}
var log = [];
snap.panels.forEach(function (e) {
    var p = panelById(e.id);
    if (!p) return;
    var present = {};
    p.widgets().forEach(function (w) { present[w.type] = true; });
    var map = {};
    e.widgets.forEach(function (s) {
        if (present[s.type]) return;           // already there: do not duplicate
        var w = p.addWidget(s.type);
        load(w, [], s.config);
        w.currentConfigGroup = [];
        w.reloadConfig();
        map[s.id] = w.id;
    });
    var order = e.order.split(";").map(function (id) { return map[id] !== undefined ? String(map[id]) : id; });
    p.currentConfigGroup = ["General"];
    p.writeConfig("AppletOrder", order.join(";"));
    p.reloadConfig();
    log.push("panel " + e.id + " order " + order.join(";"));
});
print(log.join("\n"));
