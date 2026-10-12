(() => {
  const button = document.getElementById('print-receipt');
  button.hidden = false;
  button.addEventListener('click', () => window.print());
})();
