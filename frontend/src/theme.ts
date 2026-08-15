const THEME_VARIABLES: Record<string, string> = {
  bg_color: "--tg-bg-color",
  text_color: "--tg-text-color",
  hint_color: "--tg-hint-color",
  button_color: "--tg-button-color",
  button_text_color: "--tg-button-text-color",
  secondary_bg_color: "--tg-secondary-bg-color",
};

export function applyTelegramTheme(): void {
  const webApp = window.Telegram?.WebApp;
  if (!webApp) {
    return;
  }

  for (const [telegramName, cssName] of Object.entries(THEME_VARIABLES)) {
    const value = webApp.themeParams[telegramName];
    if (value) {
      document.documentElement.style.setProperty(cssName, value);
    }
  }
}

export function setupTelegram(): void {
  const webApp = window.Telegram?.WebApp;
  if (!webApp) {
    return;
  }

  applyTelegramTheme();
  webApp.ready();
  webApp.expand();
  webApp.onEvent("themeChanged", applyTelegramTheme);
}
