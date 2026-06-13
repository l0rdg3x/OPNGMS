import { DirectionProvider, MantineProvider } from "@mantine/core";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "@mantine/charts/styles.css";
import "@mantine/dates/styles.css";
import "./styles/app.css";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { DirectionSync } from "./components/DirectionSync";
import { I18nProvider } from "./i18n";
import { detectInitialLocale, isRtl } from "./i18n/locale";
import { theme } from "./theme";

// Reflect the persisted/detected locale on <html> before first paint to avoid a direction flash.
const initialLocale = detectInitialLocale();
document.documentElement.lang = initialLocale;
document.documentElement.dir = isRtl(initialLocale) ? "rtl" : "ltr";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      <DirectionProvider initialDirection={isRtl(initialLocale) ? "rtl" : "ltr"} detectDirection={false}>
        <MantineProvider theme={theme} forceColorScheme="dark">
          <Notifications />
          <DirectionSync />
          <QueryClientProvider client={new QueryClient()}>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </QueryClientProvider>
        </MantineProvider>
      </DirectionProvider>
    </I18nProvider>
  </StrictMode>,
);
