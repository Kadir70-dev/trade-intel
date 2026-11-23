import type { Metadata, Viewport } from "next";
import { Shell } from "@/components/Shell";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trade Intel",
  description: "Sourcing CRM for UAE wholesale electronics",
  robots: { index: false, follow: false },
};
export const viewport: Viewport = { themeColor: "#0a0d14", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <body>
        <AuthProvider>
          <Shell>{children}</Shell>
        </AuthProvider>
      </body>
    </html>
  );
}
