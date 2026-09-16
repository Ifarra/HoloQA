/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${process.env.HOLOQA_API_URL || 'http://localhost:8000'}/api/:path*` }]
  },
}
export default nextConfig
