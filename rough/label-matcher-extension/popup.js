/* Popup entry point. The popup closes whenever it loses focus, so everything typed is saved
 * automatically (chrome.storage.local) and restored the next time it opens. */
LM.init({ mode: 'popup' }).then(() => {
  const btn = document.getElementById('openSide');
  btn.addEventListener('click', () => {
    // Must be called straight from the click (user gesture), so use the window id fetched at start-up.
    chrome.sidePanel.open({ windowId: LM.windowId }).then(() => window.close(), (e) => {
      document.getElementById('status').hidden = false;
      document.getElementById('status').className = 'status bad';
      document.getElementById('status').textContent = 'Could not open the sidebar: ' + e.message;
    });
  });
});
