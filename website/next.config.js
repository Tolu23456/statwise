/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  images: { unoptimized: true },
  trailingSlash: true,
}

// GitHub Pages serves this site from /statwise/, so every asset and link
// must be prefixed. Without this the HTML points at /_next/... on the
// domain root, which 404s and leaves the page completely unstyled.
//
// On Vercel the site lives at the domain root instead, and Vercel sets
// VERCEL=1 in every build — keeping /statwise there would make the whole
// site 404 (static export writes files to out/ regardless; basePath only
// rewrites the URLs inside them). So: prefix only outside Vercel.
if (process.env.VERCEL !== '1') {
  nextConfig.basePath = '/statwise';
  nextConfig.assetPrefix = '/statwise/';
}

module.exports = nextConfig
