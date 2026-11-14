// The backend origin is deployment configuration; never bake server IPs into Git.
const value = process.env.SWIFTCHART_BACKEND_ORIGIN;
if (!value) throw new Error("SWIFTCHART_BACKEND_ORIGIN must be configured before publication");
const parsed = new URL(value);
if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) {
  throw new Error("Backend origin must be an HTTP(S) origin without credentials or a path");
}
const backendOrigin = parsed.origin;
export const config = {
  "version": 2,
  "git": { "deploymentEnabled": false },
  "framework": "vite",
  "installCommand": "cd frontend && npm install",
  "buildCommand": "cd frontend && npm run build",
  "outputDirectory": "frontend/dist",
  "headers": [
    {
      "source": "/release.json",
      "headers": [
        {
          "key": "Cache-Control",
          "value": "no-store, max-age=0"
        }
      ]
    }
  ],
  "redirects": [
    {
      "source": "/",
      "has": [
        {
          "type": "host",
          "value": "swiftchart.vercel.app"
        }
      ],
      "destination": "https://swiftchart.xyz",
      "permanent": true
    },
    {
      "source": "/:path*",
      "has": [
        {
          "type": "host",
          "value": "swiftchart.vercel.app"
        }
      ],
      "destination": "https://swiftchart.xyz/:path*",
      "permanent": true
    }
  ],
  "rewrites": [
    {
      "source": "/api/(.*)",
      "destination": `${backendOrigin}/api/$1`
    },
    {
      "source": "/health",
      "destination": `${backendOrigin}/health`
    },
    {
      "source": "/(.*)",
      "destination": "/index.html"
    }
  ]
};
