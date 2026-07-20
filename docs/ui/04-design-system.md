# Design system

Working name: **AI Ops Quiet Console**.

## Color

Blue is not part of the palette. The base is a warm graphite environment with an ivory text system and low-saturation semantic accents.

### Foundation tokens

| Token | Value | Use |
|---|---:|---|
| `--canvas` | `#10120F` | app background |
| `--sidebar` | `#141712` | global navigation |
| `--surface-1` | `#181C16` | primary panels |
| `--surface-2` | `#1E231B` | raised or interactive panels |
| `--surface-3` | `#272D23` | hover, selected neutral, inset controls |
| `--line` | `#343B30` | borders and dividers |
| `--line-strong` | `#495143` | focused separation |
| `--text` | `#F0EFE7` | primary text |
| `--text-muted` | `#B2B6AA` | supporting text |
| `--text-subtle` | `#858B7F` | metadata and disabled text |

### Semantic tokens

| Token | Value | Meaning |
|---|---:|---|
| `--accent` | `#A8B889` | selected, active, primary action |
| `--accent-strong` | `#C0CE9D` | focus and high-emphasis active state |
| `--success` | `#8FB28A` | verified or complete |
| `--warning` | `#D2A45E` | needs review or degraded |
| `--danger` | `#D47C6E` | failure or destructive action |
| `--reasoning` | `#B49AC7` | teacher/model reasoning |
| `--human` | `#D8C58F` | human handoff |

Use color on small areas: a status dot, left rule, label, focus ring, selected background tint, or chart mark. Do not tint full cards by default. Every semantic color is paired with text or icon shape.

## Typography

- UI: Inter or the existing system-sans stack. Avoid adding a webfont dependency during the first pass.
- Operational data: `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`.
- Page title: `28–32px`, weight `650–700`, tight leading.
- Section title: `16–18px`, weight `600–650`.
- Body: `14px` at `1.5` line-height.
- Metadata: `12px`, never below `11px`.
- Use tabular numbers for metrics, timestamps, costs, and counts.

Sentence case is the default. Uppercase is reserved for short column headings or compact status codes; do not uppercase paragraphs or navigation.

## Spacing and shape

- Base spacing unit: `4px`.
- Common gaps: `8`, `12`, `16`, `24`, `32`.
- Page maximum width: approximately `1440px`, with denser console routes allowed to fill available width.
- Panel radius: `10–12px`; controls: `8px`; pills only for true tags/statuses.
- Use one-pixel borders and tonal separation before shadows.
- Shadows are reserved for overlays, drawers, menus, and drag states. Routine panels should not float.

## Iconography

Adopt one monochrome SVG stroke library such as `lucide-react`.

- Default size: `18px`; compact controls: `16px`; empty-state illustration: maximum `32px`.
- Stroke width: `1.75` or `2`, consistent per context.
- Icons inherit `currentColor`.
- No emoji, multicolor illustrations, AI sparkle symbols, or decorative icons beside every heading.
- Use icons for navigation, actions without enough room for text, status distinctions, and recurring object types.
- If the text already makes the meaning obvious, omit the icon.
- Icon-only buttons require an accessible name and tooltip.

Initial mapping:

| Concept | Icon |
|---|---|
| Overview | `LayoutDashboard` |
| Domains | `PanelsTopLeft` |
| Activity | `ListTree` or `Activity` |
| Learning | `GraduationCap` |
| System | `Settings2` |
| Career Search | `BriefcaseBusiness` |
| Marketplace | `Store` |
| Needs attention | `CircleAlert` |
| Verified | `CircleCheck` |
| Human handoff | `Hand` |
| Running | `LoaderCircle` with reduced-motion fallback |

## Components

The minimum primitive set:

- `AppShell`, `GlobalNav`, `PageHeader`, `Breadcrumbs`, `LocalTabs`
- `Button`, `IconButton`, `Menu`, `Tooltip`
- `Input`, `Select`, `Checkbox`, `SegmentedControl`, `Field`
- `Badge`, `StatusDot`, `Tag`
- `Panel`, `Stat`, `EmptyState`, `Callout`
- `DataTable`, `Toolbar`, `FilterChip`, `Pagination`
- `Drawer`, `Dialog`, `Toast`
- `Timeline`, `ProgressTrack`, `MiniBar`, `Sparkline`
- `Skeleton`, `ErrorState`, `StaleState`

One component owns each state pattern. Features may compose primitives but should not redefine buttons, badges, panels, or status colors.

## Motion

- Duration: `120–180ms` for controls, `200–240ms` for drawers and page transitions.
- Prefer opacity and small translations; avoid scale bounce.
- The sidebar does not slide between unrelated menu systems.
- Loading indicators do not animate when `prefers-reduced-motion: reduce` is active.
- No pulsing background for persistent warnings. A stable, high-contrast banner is easier to read and less fatiguing.

## Data visualization

Visuals answer a question, not decorate a card.

- Pipeline: segmented horizontal track with counts and conversion.
- State over time: sparkline or compact area chart.
- Composition: stacked bar with direct labels.
- Coverage: matrix/heatmap by state and domain.
- Sequence: vertical timeline or stepper.
- Relationship/topology: restrained node-link diagram only where topology is the task.

Avoid gauges, unlabeled donuts, rainbow charts, gradients, and chart legends that force color matching. Use direct labels, tooltips, and accessible summaries.

## Accessibility

- WCAG 2.2 AA contrast for text and controls.
- Supporting text targets at least `4.5:1` on every surface where it can render; compact metadata and uppercase table headings are not exempt.
- A component owns its foreground and background as a pair. Never apply dark-theme text tokens to a legacy light card, or retain legacy dark text on a graphite surface.
- Visible warm focus ring; never remove outline without a replacement.
- Minimum target size `40px` on desktop and `44px` on touch layouts.
- Full keyboard navigation through global nav, local tabs, tables, labeler, drawers, and dialogs.
- Use real landmarks and headings; active navigation uses `aria-current`.
- Live updates are polite by default and must not announce every Activity row.
- Provide a density toggle for data-heavy screens, not a global font-size reduction.
