import type { Metadata } from "next";
import { Atkinson_Hyperlegible } from "next/font/google";
import "./globals.css";

/* Atkinson Hyperlegible is the design system's choice for a civic product: it was
   drawn to keep letterforms distinguishable for low-vision readers, which is the
   right bias for software a volunteer coordinator reads in a hurry. */
const atkinson = Atkinson_Hyperlegible({
  variable: "--font-atkinson",
  weight: ["400", "700"],
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Zamu — coverage",
  description:
    "An agent that keeps a volunteer roster covered: finds the gap, asks the fairest qualified person, verifies the roster changed.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${atkinson.variable} h-full antialiased`}>
      <body className="min-h-full">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-accent focus:px-4 focus:py-3 focus:text-on-accent focus:font-bold"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
