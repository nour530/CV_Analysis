// Apply a saved light/dark choice before the app renders (avoids a flash).
// A separate file rather than an inline script, so the Content-Security-Policy
// can allow scripts from this site only ('self'), with no inline exceptions.
try {
  var theme = localStorage.getItem('jobmatch.theme');
  if (theme === 'light' || theme === 'dark') {
    document.documentElement.setAttribute('data-theme', theme);
  }
} catch (error) {}
