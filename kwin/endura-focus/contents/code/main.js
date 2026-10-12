// Relay window activation to endura-nap over D-Bus.
function relay(w) {
    callDBus("io.github.Endura.Nap", "/io/github/Endura/Nap", "io.github.Endura.Nap",
             "Focus", w ? w.pid : 0, w ? String(w.resourceClass) : "");
}
workspace.windowActivated.connect(relay);
relay(workspace.activeWindow);
