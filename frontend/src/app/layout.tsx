import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "QualiBug Console",
  description: "QualiBug 商用前端",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="min-h-dvh bg-[var(--bg)] text-[var(--fg)] antialiased">
        {children}
      </body>
    </html>
  );
}
