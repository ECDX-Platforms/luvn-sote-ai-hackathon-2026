// Shared story helpers for the App shell and Layouts stories. Not exported from the library.
import type { ReactNode } from 'react';
import { ChartColumn, ClipboardList, FileText, House, MessageSquare, Users } from 'lucide-react';
import { Block } from '../Block';
import { BottomNav } from '../BottomNav';
import { Breadcrumb } from '../Breadcrumb';
import { Header } from '../Header';
import { NavItem } from '../NavItem';
import { Search } from '../Search';
import { Sidebar } from '../Sidebar';
import { AppShell } from './AppShell';

const icon = (Glyph: typeof House) => <Glyph size={16} strokeWidth={1.5} />;

const sideItems = [
  { href: '#home', label: 'Home', glyph: House },
  { href: '#owners', label: 'Owners', glyph: Users },
  { href: '#documents', label: 'Documents', glyph: FileText },
  { href: '#reports', label: 'Reports', glyph: ChartColumn },
  { href: '#tasks', label: 'Tasks', glyph: ClipboardList },
  { href: '#messages', label: 'Messages', glyph: MessageSquare },
];
// The bottom bar takes 4 items (5 at most): Home, Documents, Tasks, Messages.
const bottomItems = [sideItems[0], sideItems[2], sideItems[4], sideItems[5]];

const navItems = (list: typeof sideItems) =>
  list.map((item) => (
    <NavItem key={item.href} href={item.href} icon={icon(item.glyph)} selected={item.href === '#home'}>
      {item.label}
    </NavItem>
  ));

/** The Figma breakpoints as Storybook viewports (viewport toolbar). */
export const layoutViewports = {
  viewport: {
    options: {
      desktop: { name: 'Desktop 1440', styles: { width: '1440px', height: '900px' }, type: 'desktop' },
      laptop: { name: 'Laptop 1024', styles: { width: '1024px', height: '900px' }, type: 'desktop' },
      phone: { name: 'Phone 375', styles: { width: '375px', height: '812px' }, type: 'mobile' },
      reflow: { name: 'Reflow 320', styles: { width: '320px', height: '640px' }, type: 'mobile' },
    },
  },
};

export const atViewport = (value: 'desktop' | 'laptop' | 'phone' | 'reflow') => ({ viewport: { value, isRotated: false } });

/** A block with a neutral title and one line of text. */
export const sectionBlock = (title: string) => (
  <Block key={title} title={title}>
    <p className="prose">Section content. Any content, one topic.</p>
  </Block>
);

/** The full shell with neutral content: sidebar, header, bottom navigation on phones. */
export function StoryShell({ children, identityStrip }: { children: ReactNode; identityStrip?: ReactNode }) {
  return (
    <AppShell
      sidebar={<Sidebar productName="Product name">{navItems(sideItems)}</Sidebar>}
      header={
        <Header
          title="Overview"
          breadcrumb={<Breadcrumb items={[{ label: 'Home', href: '#home' }]} current="Overview" />}
          search={<Search label="Search items" placeholder="Search items" shortcut="k" />}
          notifications={{ label: 'Notifications, 3 new', count: 3 }}
          user={{ name: 'User name', initials: 'UN' }}
        />
      }
      identityStrip={identityStrip}
      bottomNav={<BottomNav>{navItems(bottomItems)}</BottomNav>}
    >
      {children}
    </AppShell>
  );
}
