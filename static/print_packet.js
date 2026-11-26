document.addEventListener('DOMContentLoaded', () => {
  const button = document.getElementById('print-packet');
  if (button) button.addEventListener('click', () => window.print());
});
