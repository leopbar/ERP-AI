export default {
  experimental: { proxyTimeout: 600000 },
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${process.env.API_PROXY_URL || 'http://localhost:8001'}/api/:path*` }];
  },
};
