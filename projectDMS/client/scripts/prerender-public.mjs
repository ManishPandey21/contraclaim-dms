import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const clientRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const distDir = resolve(clientRoot, "dist");
const ssrDir = resolve(clientRoot, ".seo-ssr");
const serverEntry = resolve(ssrDir, "entry-server.js");

const escapeHtml = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");

const escapeXml = (value) =>
  escapeHtml(value).replaceAll("'", "&apos;");

const jsonForScript = (value) =>
  JSON.stringify(value).replaceAll("<", "\\u003c");

function applicationShell(source) {
  return source
    .replace(/<title>[\s\S]*?<\/title>/i, "<title>Secure application | ContraClaim DMS</title>")
    .replace(
      /<meta\s+name="description"[\s\S]*?\/>/i,
      '<meta name="description" content="Private ContraClaim DMS application shell." />',
    )
    .replace(/\s*<meta\s+name="keywords"[\s\S]*?\/>/i, "")
    .replace(/\s*<link\s+rel="canonical"[^>]*>/gi, "")
    .replace(/\s*<meta\s+(?:property="og:[^"]+"|name="twitter:[^"]+")[^>]*>/gi, "")
    .replace(/\s*<script\s+type="application\/ld\+json"[^>]*>[\s\S]*?<\/script>/gi, "")
    .replace(
      /<meta\s+name="robots"[^>]*>/i,
      '<meta name="robots" content="noindex, nofollow" />',
    );
}

function publicHead(page) {
  const canonical = `https://web.contraclaim.com${page.path === "/" ? "/" : page.path}`;
  const imageUrl = /^https?:\/\//i.test(page.image.url)
    ? page.image.url
    : `https://web.contraclaim.com${page.image.url.startsWith("/") ? "" : "/"}${page.image.url}`;
  const tags = [
    `<link rel="canonical" href="${escapeHtml(canonical)}" />`,
    `<meta property="og:type" content="${escapeHtml(page.ogType)}" />`,
    '<meta property="og:site_name" content="ContraClaim DMS" />',
    `<meta property="og:title" content="${escapeHtml(page.title)}" />`,
    `<meta property="og:description" content="${escapeHtml(page.description)}" />`,
    `<meta property="og:url" content="${escapeHtml(canonical)}" />`,
    `<meta property="og:image" content="${escapeHtml(imageUrl)}" />`,
    '<meta property="og:image:width" content="1200" />',
    '<meta property="og:image:height" content="800" />',
    `<meta property="og:image:alt" content="${escapeHtml(page.image.alt)}" />`,
    '<meta name="twitter:card" content="summary_large_image" />',
    `<meta name="twitter:title" content="${escapeHtml(page.title)}" />`,
    `<meta name="twitter:description" content="${escapeHtml(page.description)}" />`,
    `<meta name="twitter:image" content="${escapeHtml(imageUrl)}" />`,
    `<meta name="twitter:image:alt" content="${escapeHtml(page.image.alt)}" />`,
  ];

  if (page.publishedTime) {
    tags.push(`<meta property="article:published_time" content="${escapeHtml(page.publishedTime)}" />`);
  }
  if (page.modifiedTime) {
    tags.push(`<meta property="article:modified_time" content="${escapeHtml(page.modifiedTime)}" />`);
  }
  for (const document of page.jsonLd) {
    tags.push(`<script type="application/ld+json">${jsonForScript(document)}</script>`);
  }
  return tags.map((tag) => `    ${tag}`).join("\n");
}

function renderDocument(shell, page, body) {
  const withHead = shell
    .replace(/<title>[\s\S]*?<\/title>/i, `<title>${escapeHtml(page.title)}</title>`)
    .replace(
      /<meta\s+name="description"[^>]*>/i,
      `<meta name="description" content="${escapeHtml(page.description)}" />`,
    )
    .replace(
      /<meta\s+name="robots"[^>]*>/i,
      '<meta name="robots" content="index, follow" />',
    )
    .replace("    <!-- PUBLIC_SEO -->", publicHead(page));

  return withHead.replace(
    '<div id="root"></div>',
    `<div id="root" data-prerendered="true">${body}</div>`,
  );
}

function outputFileFor(pathname) {
  return pathname === "/"
    ? resolve(distDir, "index.html")
    : resolve(distDir, `.${pathname}`, "index.html");
}

function sitemapFor(pages) {
  const rows = pages.map((page) => {
    const loc = `https://web.contraclaim.com${page.path === "/" ? "/" : page.path}`;
    return [
      "  <url>",
      `    <loc>${escapeXml(loc)}</loc>`,
      ...(page.lastmod ? [`    <lastmod>${escapeXml(page.lastmod)}</lastmod>`] : []),
      "  </url>",
    ].join("\n");
  });
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...rows,
    "</urlset>",
    "",
  ].join("\n");
}

try {
  const source = await readFile(resolve(distDir, "index.html"), "utf8");
  const shell = applicationShell(source);
  await writeFile(resolve(distDir, "app-shell.html"), shell, "utf8");

  const server = await import(`${pathToFileURL(serverEntry).href}?v=${Date.now()}`);
  const pages = server.getIndexablePublicPages();
  const titles = new Set();
  const descriptions = new Set();

  for (const page of pages) {
    if (titles.has(page.title)) throw new Error(`Duplicate public title: ${page.title}`);
    if (descriptions.has(page.description)) {
      throw new Error(`Duplicate public description: ${page.description}`);
    }
    titles.add(page.title);
    descriptions.add(page.description);

    const html = renderDocument(shell, page, server.renderPublicPage(page.path));
    const output = outputFileFor(page.path);
    await mkdir(dirname(output), { recursive: true });
    await writeFile(output, html, "utf8");
  }

  await writeFile(resolve(distDir, "sitemap.xml"), sitemapFor(pages), "utf8");
  await writeFile(
    resolve(distDir, "public-routes.json"),
    `${JSON.stringify(pages.map((page) => page.path), null, 2)}\n`,
    "utf8",
  );
  console.log(`Prerendered ${pages.length} indexable public pages.`);
} finally {
  await rm(ssrDir, { recursive: true, force: true });
}
