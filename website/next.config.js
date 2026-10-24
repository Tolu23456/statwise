/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  images: { unoptimized: true },
  trailingSlash: true,
  // GitHub Pages serves this site from /statwise/, so every asset and link
  // must be prefixed. Without this the HTML points at /_next/... on the
  // domain root, which 404s and leaves the page completely unstyled.
  basePath: '/statwise',
  assetPrefix: '/statwise/',
}

module.exports = nextConfig
