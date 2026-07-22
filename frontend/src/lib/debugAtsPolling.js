/**
 * Debug utility for ATS polling.
 * 
 * Enable debug logging in browser console:
 *   window.__DEBUG_ATS_POLLING = true
 * 
 * Disable:
 *   window.__DEBUG_ATS_POLLING = false
 * 
 * Then reload the page or open the ATS modal to see detailed logs
 * about when polling starts, stops, and which statuses are reached.
 */

export const enableAtsPollingDebug = () => {
  window.__DEBUG_ATS_POLLING = true;
  console.log("✓ ATS polling debug enabled. Reload page or open ATS modal to see logs.");
};

export const disableAtsPollingDebug = () => {
  window.__DEBUG_ATS_POLLING = false;
  console.log("✓ ATS polling debug disabled.");
};

// Auto-detect if running in development with ?debug=ats in URL
if (typeof window !== "undefined" && window.location.search.includes("debug=ats")) {
  enableAtsPollingDebug();
}
