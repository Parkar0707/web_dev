/* Sidebar entry point. The side panel is global (not tied to one tab), so it stays open while the
 * user switches or closes tabs. It follows the ACTIVE tab of its own window and re-checks it
 * (optionally) whenever the user switches to another tab or the page finishes loading. */
LM.init({ mode: 'sidebar' }).then(() => {
  const win = LM.windowId;

  chrome.tabs.onActivated.addListener((info) => {
    if (win == null || info.windowId === win) LM.tabChanged('switch');
  });

  chrome.tabs.onUpdated.addListener((_id, change, tab) => {
    if (!tab.active || (win != null && tab.windowId !== win)) return;
    if (change.status === 'complete') LM.tabChanged('loaded');
    else if (change.title) LM.tabChanged('title');
  });

  // a tab was closed: Chrome activates another one (onActivated fires), but refresh in case the window was emptied
  chrome.tabs.onRemoved.addListener(() => LM.tabChanged('switch'));
});
