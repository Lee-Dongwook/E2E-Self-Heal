import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)

const GITHUB_ORG = 'Lee-Dongwook';
const GITHUB_REPO = 'E2E-Self-Heal';
const GITHUB_URL = `https://github.com/${GITHUB_ORG}/${GITHUB_REPO}`;

const config: Config = {
  title: 'E2E-Healer',
  tagline:
    'Self-healing Playwright E2E tests — auto-patch broken selectors, or review them at the source.',
  favicon: 'img/favicon.ico',

  // Future flags, see https://docusaurus.io/docs/api/docusaurus-config#future
  future: {
    v4: true, // Improve compatibility with the upcoming Docusaurus v4
  },

  // Production URL (GitHub Pages). Served under /<repo>/.
  url: `https://${GITHUB_ORG.toLowerCase()}.github.io`,
  baseUrl: `/${GITHUB_REPO}/`,

  // GitHub pages deployment config.
  organizationName: GITHUB_ORG,
  projectName: GITHUB_REPO,

  onBrokenLinks: 'throw',

  // Even if you don't use internationalization, you can use this field to set
  // useful metadata like html lang. For example, if your site is Chinese, you
  // may want to replace "en" with "zh-Hans".
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: `${GITHUB_URL}/tree/main/docs-site/`,
        },
        // Blog is intentionally disabled for now — the default Docusaurus demo
        // posts were removed. Re-enable (and add a `blog/` dir) when we start
        // publishing release notes.
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    // Replace with your project's social card
    image: 'img/docusaurus-social-card.jpg',
    // Dark-mode-first (see docs-site/DESIGN.md). Always start in dark;
    // the toggle stays available, but we don't defer to the OS setting.
    colorMode: {
      defaultMode: 'dark',
      respectPrefersColorScheme: false,
    },
    navbar: {
      title: 'E2E-Healer',
      logo: {
        alt: 'E2E-Healer Logo',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'tutorialSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          href: GITHUB_URL,
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {
              label: 'Getting Started',
              to: '/docs/getting-started/introduction',
            },
          ],
        },
        {
          title: 'Project',
          items: [
            {
              label: 'Contributing',
              href: `${GITHUB_URL}/blob/main/CONTRIBUTING.md`,
            },
            {
              label: 'Code of Conduct',
              href: `${GITHUB_URL}/blob/main/CODE_OF_CONDUCT.md`,
            },
            {
              label: 'Security',
              href: `${GITHUB_URL}/blob/main/SECURITY.md`,
            },
            {
              label: 'License',
              href: `${GITHUB_URL}/blob/main/LICENSE`,
            },
          ],
        },
        {
          title: 'More',
          items: [
            {
              label: 'GitHub',
              href: GITHUB_URL,
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} E2E-Healer. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
