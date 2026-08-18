interface TelegramWebApp {
  initData: string;
  ready(): void;
  expand(): void;
  openTelegramLink(url: string): void;
  themeParams: Record<string, string>;
  onEvent(type: "themeChanged", callback: () => void): void;
  colorScheme: "light" | "dark";
}

interface Window {
  Telegram?: {
    WebApp: TelegramWebApp;
  };
}
