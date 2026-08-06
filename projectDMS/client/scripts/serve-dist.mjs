import { createReadStream } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { brotliCompress, gzip } from "node:zlib";
import { promisify } from "node:util";

const compressBr = promisify(brotliCompress);
const compressGzip = promisify(gzip);
const scriptDir = resolve(fileURLToPath(new URL(".", import.meta.url)));
const distDir = resolve(scriptDir, "../dist");
const port = Number.parseInt(process.env.PORT || "5173", 10);

const publicRoutes = new Set(
  JSON.parse(await readFile(resolve(distDir, "public-routes.json"), "utf8")),
);

const privatePrefixes = [
  "/login", "/security-terms", "/overview", "/dashboard", "/organizations",
  "/projects", "/documents", "/documentsearch", "/documentviewer", "/tags",
  "/profile", "/users", "/permissions", "/settings", "/plan-settings",
  "/subscription-management", "/notifications", "/legal-words", "/billing",
  "/upload", "/share", "/email-groups", "/register", "/folders", "/tasks",
  "/parties", "/letters", "/letter-quality", "/reports", "/claims", "/sla",
  "/key-dates", "/variations", "/bank-guarantees", "/insurance", "/ipc-bills",
  "/concerns", "/admin", "/retrieval-console", "/observability",
  "/letter-templates", "/representatives", "/contracts", "/chronology",
  "/arbitration", "/reference", "/health",
];

const mimeTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".pdf": "application/pdf",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".webmanifest": "application/manifest+json",
  ".webp": "image/webp",
  ".xml": "application/xml; charset=utf-8",
};

const securityHeaders = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "SAMEORIGIN",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
};

function isPrivateRoute(pathname) {
  return privatePrefixes.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

function publicFile(pathname) {
  return pathname === "/"
    ? resolve(distDir, "index.html")
    : resolve(distDir, `.${pathname}`, "index.html");
}

function safeStaticFile(pathname) {
  const relative = pathname.replace(/^\/+/, "");
  const candidate = resolve(distDir, relative);
  return candidate === distDir || candidate.startsWith(`${distDir}${sep}`)
    ? candidate
    : null;
}

async function existingFile(path) {
  if (!path) return null;
  try {
    const info = await stat(path);
    return info.isFile() ? info : null;
  } catch {
    return null;
  }
}

async function sendFile(request, response, path, statusCode = 200) {
  const info = await existingFile(path);
  if (!info) return false;

  const extension = extname(path).toLowerCase();
  const type = mimeTypes[extension] || "application/octet-stream";
  const etag = `W/\"${info.size.toString(16)}-${Math.floor(info.mtimeMs).toString(16)}\"`;
  const immutable = /[.-][A-Za-z0-9_-]{8,}\.(?:js|css)$/i.test(path);
  const isHtml = extension === ".html";
  const cacheControl = immutable
    ? "public, max-age=31536000, immutable"
    : isHtml
      ? "no-cache"
      : "public, max-age=86400";

  if (request.headers["if-none-match"] === etag) {
    response.writeHead(304, { ...securityHeaders, ETag: etag, "Cache-Control": cacheControl });
    response.end();
    return true;
  }

  const headers = {
    ...securityHeaders,
    ...(String(request.headers.host || "").split(":")[0] === "web.contraclaim.com"
      ? {}
      : { "X-Robots-Tag": "noindex, nofollow" }),
    "Content-Type": type,
    "Cache-Control": cacheControl,
    ETag: etag,
    Vary: "Accept-Encoding",
  };

  if (request.method === "HEAD") {
    response.writeHead(statusCode, { ...headers, "Content-Length": info.size });
    response.end();
    return true;
  }

  const canCompress = /^(?:text\/|application\/(?:javascript|json|xml))/.test(type);
  const accepted = String(request.headers["accept-encoding"] || "");
  if (canCompress && info.size > 1024 && accepted.includes("br")) {
    const body = await compressBr(await readFile(path));
    response.writeHead(statusCode, { ...headers, "Content-Encoding": "br", "Content-Length": body.length });
    response.end(body);
    return true;
  }
  if (canCompress && info.size > 1024 && accepted.includes("gzip")) {
    const body = await compressGzip(await readFile(path));
    response.writeHead(statusCode, { ...headers, "Content-Encoding": "gzip", "Content-Length": body.length });
    response.end(body);
    return true;
  }

  response.writeHead(statusCode, { ...headers, "Content-Length": info.size });
  createReadStream(path).pipe(response);
  return true;
}

const server = createServer(async (request, response) => {
  try {
    if (request.method !== "GET" && request.method !== "HEAD") {
      response.writeHead(405, { ...securityHeaders, Allow: "GET, HEAD" });
      response.end();
      return;
    }

    let pathname;
    try {
      pathname = decodeURIComponent(new URL(request.url || "/", "http://localhost").pathname);
    } catch {
      response.writeHead(400, securityHeaders);
      response.end("Bad request");
      return;
    }

    const withoutTrailingSlash = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
    if (pathname !== withoutTrailingSlash && publicRoutes.has(withoutTrailingSlash)) {
      response.writeHead(308, { ...securityHeaders, Location: withoutTrailingSlash });
      response.end();
      return;
    }

    if (publicRoutes.has(withoutTrailingSlash)) {
      await sendFile(request, response, publicFile(withoutTrailingSlash));
      return;
    }

    const staticPath = safeStaticFile(pathname);
    if (await sendFile(request, response, staticPath)) return;

    if (isPrivateRoute(pathname)) {
      await sendFile(request, response, resolve(distDir, "app-shell.html"));
      return;
    }

    if (extname(pathname)) {
      response.writeHead(404, { ...securityHeaders, "Content-Type": "text/plain; charset=utf-8" });
      response.end("Not found");
      return;
    }

    await sendFile(request, response, resolve(distDir, "app-shell.html"), 404);
  } catch (error) {
    console.error(error);
    response.writeHead(500, securityHeaders);
    response.end("Internal server error");
  }
});

server.listen(port, "0.0.0.0", () => {
  console.log(`ContraClaim client listening on ${port}`);
});
