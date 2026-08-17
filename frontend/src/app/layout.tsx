import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ClerkProvider } from "@clerk/nextjs";
import Link from "next/link";
import { AuthHeader } from "@/components/auth-header";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AI-ETL",
  description: "Agentic ETL — upload messy data, ask a business question.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <ClerkProvider>
      <html
        lang="en"
        className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      >
        <body className="min-h-full flex flex-col">
          <header className="flex justify-between items-center p-4 gap-4 h-16 border-b">
            <nav className="flex gap-4 text-sm font-medium">
              <Link href="/">Executar</Link>
              <Link href="/historico">Histórico</Link>
            </nav>
            <AuthHeader />
          </header>
          {children}
        </body>
      </html>
    </ClerkProvider>
  );
}
