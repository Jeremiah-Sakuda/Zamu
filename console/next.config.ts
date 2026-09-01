import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone so the container image carries only what it runs, rather than the
  // whole node_modules tree.
  output: "standalone",

  // The console is a client of the Zamu service and holds nothing of its own, so
  // there is nothing here to index and nothing to leak in a header.
  poweredByHeader: false,

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
