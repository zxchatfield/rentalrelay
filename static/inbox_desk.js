'use strict';
const button = document.querySelector('#copy-draft');
if (button) {
  button.addEventListener('click', async () => {
    const element = document.getElementById(button.dataset.copyTarget);
    if (!element) return;
    try {
      await navigator.clipboard.writeText(element.textContent.trim());
      button.textContent = 'Copied!';
    } catch (_) {
      button.textContent = 'Select the text above to copy';
    }
  });
}
