# Family Expenses

A simple, private expense tracker for a household. It's a single self-contained
web page — no accounts, no server, no build step. Just open it and start
tracking.

## How to use it

Open `index.html` in any modern browser (double-click the file, or serve the
folder). That's it.

- **Add an expense** — date, amount, category, who paid, and an optional note.
- **Edit / delete** any row from the table.
- **Switch months** with the picker in the top bar.
- **Categories & totals** — spending is grouped by category with a donut chart
  and per-category breakdown.
- **Budgets** — set a monthly limit per category; the bars fill as you spend and
  turn red when you go over.
- **Filter** the expense list by category.

## Where your data lives

Everything is saved in your browser's `localStorage`. It never leaves your
device — there's no backend. Because of that:

- Data is tied to **this browser on this device**. It won't sync across phones
  or laptops.
- Clearing your browser data will erase it.

Use **Export** (top bar) to download a JSON backup, and **Import** to restore it
or move it to another device/browser. Backing up regularly is recommended.

## Roadmap ideas

This is intentionally a v1 built to be usable immediately. Natural next steps:

- Recurring expenses and income tracking
- Multi-device sync (would require a backend + database)
- CSV export and receipt attachments
- Shared access for multiple family members

## Tech

Plain HTML, CSS, and vanilla JavaScript in one file — no dependencies, works
offline. Charts are drawn with inline SVG.
