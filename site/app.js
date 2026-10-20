'use strict';

const menu = document.querySelector('.menu-toggle');
const navigation = document.querySelector('#navigation');
function closeMenu() {
  menu.setAttribute('aria-expanded', 'false');
  menu.setAttribute('aria-label', 'Open navigation');
  navigation.classList.remove('open');
}
menu.addEventListener('click', () => {
  const open = menu.getAttribute('aria-expanded') !== 'true';
  menu.setAttribute('aria-expanded', String(open));
  menu.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  navigation.classList.toggle('open', open);
});
navigation.addEventListener('click', event => { if (event.target.closest('a')) closeMenu(); });
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && menu.getAttribute('aria-expanded') === 'true') { closeMenu(); menu.focus(); }
});
document.addEventListener('click', event => {
  if (!navigation.contains(event.target) && !menu.contains(event.target)) closeMenu();
});

function tabs(tablist, select) {
  const buttons = [...tablist.querySelectorAll('[role="tab"]')];
  function activate(button, focus = false) {
    buttons.forEach(item => { item.setAttribute('aria-selected', String(item === button)); item.tabIndex = item === button ? 0 : -1; });
    select(button);
    if (focus) button.focus();
  }
  buttons.forEach(button => button.addEventListener('click', () => activate(button)));
  tablist.addEventListener('keydown', event => {
    const vertical = tablist.getAttribute('aria-orientation') === 'vertical';
    const next = vertical ? 'ArrowDown' : 'ArrowRight';
    const previous = vertical ? 'ArrowUp' : 'ArrowLeft';
    if (![next, previous, 'Home', 'End'].includes(event.key)) return;
    const index = buttons.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    const target = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === next ? 1 : -1) + buttons.length) % buttons.length;
    activate(buttons[target], true);
  });
}
const views = {
  overview: {src: 'assets/studio-overview.png', alt: 'HoloQA desktop overview with plan coverage, saved-run history, and project readiness for an example storefront project.'},
  paused: {src: 'assets/studio-paused.png', alt: 'HoloQA desktop paused run with visible Resume and Stop controls, HOLD status, and an evidence inspection area.'},
};
const studioImage = document.querySelector('#studio-image');
const studioPanel = document.querySelector('#studio-panel');
tabs(document.querySelector('[aria-label="Desktop views"]'), button => {
  const view = views[button.dataset.view];
  studioImage.src = view.src;
  studioImage.alt = view.alt;
  studioPanel.setAttribute('aria-labelledby', button.id);
});
tabs(document.querySelector('[aria-label="Explore the QA workflow"]'), button => {
  document.querySelectorAll('.tour-scene').forEach(scene => { scene.hidden = scene.id !== `tour-${button.dataset.step}`; });
  document.querySelector('#workflow-panel').setAttribute('aria-labelledby', button.id);
});
const dialog = document.querySelector('#screenshot-dialog');
document.querySelector('.expand-view').addEventListener('click', () => {
  const image = dialog.querySelector('img');
  image.src = studioImage.src; image.alt = studioImage.alt;
  dialog.showModal();
});
document.querySelector('.dialog-close').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } });
let toastTimer;
function announce(message) {
  const toast = document.querySelector('.toast');
  toast.textContent = message; toast.classList.add('visible');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove('visible'), 4000);
}
document.querySelector('.copy-command').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(document.querySelector('#setup-commands').textContent);
    announce('Setup commands copied.');
  } catch {
    announce('Clipboard unavailable. Select the commands to copy them.');
  }
});

// Progressive enhancement: the verified 0.3.1 links work without this request.
// Public GitHub metadata keeps the download current; no account or tracking.
async function refreshRelease() {
  try {
    const response = await fetch('https://api.github.com/repos/Ifarra/HoloQA/releases?per_page=20', {signal: AbortSignal.timeout(5000), headers: {Accept: 'application/vnd.github+json'}});
    if (!response.ok) return;
    const releases = await response.json();
    if (!Array.isArray(releases)) return;
    for (const release of releases) {
      if (release.draft || !/^desktop-v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$/.test(release.tag_name) || !Array.isArray(release.assets)) continue;
      const version = release.tag_name.slice('desktop-v'.length);
      const asset = release.assets.find(item => item.name === `HoloQA-Desktop-${version}-windows-x64-setup.exe`);
      if (!asset || !asset.browser_download_url) continue;
      const url = new URL(asset.browser_download_url);
      const expected = `https://github.com/Ifarra/HoloQA/releases/download/${release.tag_name}/${asset.name}`;
      if (url.href !== expected) continue;
      document.querySelectorAll('[data-download]').forEach(link => { link.href = expected; });
      document.querySelectorAll('[data-release-link]').forEach(link => { link.href = `https://github.com/Ifarra/HoloQA/releases/tag/${release.tag_name}`; });
      document.querySelectorAll('[data-version]').forEach(label => { label.textContent = `v${version}${release.prerelease ? ' Preview' : ''}`; });
      return;
    }
  } catch { /* Offline/rate-limit fallback keeps all existing links usable. */ }
}
void refreshRelease();
