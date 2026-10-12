// Frontend runtime config.
// API_BASE "" = same origin (local `ppfe serve`, Docker, or any host serving both).
// For GitHub Pages, the deploy workflow rewrites this from the repo variable PPFE_API_BASE
// to point at wherever the backend runs (cloud VM, tunnel to a lab GPU box, ...).
window.PPFE_CONFIG = {
  API_BASE: "",
};
