"""Dated MarketBeat earnings tables; keep consensus separate from GAAP growth.

Revenue estimates are published rounded. An actual inside that rounding interval is
shown as inline, rather than giving a tiny apparent surprise a false sign.
"""
import hashlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser

NASDAQ = set('ISRG NFLX TSLA GOOGL MSFT META AMZN AAPL INTC TXN PEP CMCSA TMUS HON SBUX'.split())


class History(HTMLParser):
    def __init__(self):
        super().__init__(); self.active = False; self.rows = []; self.cells = None; self.cell = None; self.link = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'table' and a.get('id') == 'earnings-history': self.active = True
        if not self.active: return
        if tag == 'tr': self.cells = []; self.link = None
        if tag == 'td': self.cell = []
        if tag == 'a' and '/earnings/reports/' in a.get('href', ''): self.link = 'https://www.marketbeat.com' + a['href']

    def handle_data(self, text):
        if self.active and self.cell is not None: self.cell.append(text)

    def handle_endtag(self, tag):
        if not self.active: return
        if tag == 'td' and self.cell is not None:
            self.cells.append(' '.join(''.join(self.cell).split())); self.cell = None
        if tag == 'tr' and self.cells and len(self.cells) >= 8: self.rows.append((self.cells, self.link))
        if tag == 'table': self.active = False


def amount(text):
    m = re.fullmatch(r'\s*\$?([\d,]+(?:\.\d+)?)([BMT]?)\s*', text)
    if not m: return None, None
    value = Decimal(m[1].replace(',', '')); scale = {'': 1, 'B': 10**9, 'M': 10**6, 'T': 10**12}[m[2]]
    quantum = Decimal(10) ** value.as_tuple().exponent * scale
    return float(value * scale), float(quantum / 2)


def parse(html, url, observed_at):
    parser = History(); parser.feed(html); rows = []
    for cells, link in parser.rows:
        try: day = datetime.strptime(cells[0], '%m/%d/%Y').date().isoformat()
        except ValueError: continue
        ee, _ = amount(cells[2]); ea, _ = amount(cells[3]); gaap, _ = amount(cells[5])
        re_, rounding = amount(cells[6]); ra, ar = amount(cells[7])
        rows.append(dict(date=day, quarter=cells[1], eps_est=ee, eps_act=ea, gaap_eps=gaap,
                         rev_est=re_, rev_act=ra, rev_est_half_rounding=rounding,
                         rev_act_half_rounding=ar, source_url=url, event_url=link,
                         observed_at=observed_at, source_sha256=hashlib.sha256(html.encode()).hexdigest(),
                         source_cells=cells[:8], provider='MarketBeat (Fiscal.ai earnings data)'))
    if not rows: raise ValueError('No dated earnings-history rows; consensus unavailable')
    return rows


def history(ticker, fetch):
    exchange = 'NASDAQ' if ticker in NASDAQ else 'NYSE'
    url = f'https://www.marketbeat.com/stocks/{exchange}/{ticker}/earnings/'
    raw = fetch(url, ttl=3 * 3600, headers={'User-Agent': 'Mozilla/5.0'})
    return parse(raw, url, datetime.now(timezone.utc).replace(microsecond=0).isoformat())


def compare(row, record):
    """Only same-date, same-quarter comparisons with revenue tied to the release."""
    if not row.get('filed') or not 0 <= (date.fromisoformat(row['filed']) - date.fromisoformat(record['date'])).days <= 3: return {}
    rev, eps = row.get('revenue') or {}, row.get('eps') or {}
    compared_rev = row.get('consensus_revenue') or rev
    compared_eps = row.get('consensus_eps') or {}
    label = row.get('fiscal_label', '').lower().replace('-', ' ')
    quarter = next((n for words, n in [('first|1st|q1',1), ('second|2nd|q2',2), ('third|3rd|q3',3), ('fourth|4th|q4',4)]
                    if re.search(r'\b(?:' + words + r')\b', label)), None)
    years = re.findall(r'\b20\d{2}\b', label)
    if quarter is None or len(years) != 1 or record['quarter'] != f'Q{quarter} {years[0]}': return {}
    out = {k: record[k] for k in ('eps_est', 'eps_act', 'rev_est', 'rev_act')}
    out.update(consensus=record, eps_basis='adjusted' if record['eps_act'] != record['gaap_eps'] else 'consensus-comparable')
    if compared_rev.get('current') is not None and record['rev_act'] is not None:
        if abs(compared_rev['current'] - record['rev_act']) <= (record['rev_act_half_rounding'] or 0) + compared_rev.get('current_half_rounding', 0) + 1:
            out['rev_actual_used'] = compared_rev['current']
            out['rev_basis'] = compared_rev.get('label', 'consolidated revenue')
            if record['rev_est'] and record['rev_est'] > 0:
                delta = Decimal(str(compared_rev['current'])) - Decimal(str(record['rev_est']))
                inline = abs(delta) <= Decimal(str(record['rev_est_half_rounding']))
                out['rev_surprise'] = 0.0 if inline else float(delta / Decimal(str(record['rev_est'])))
                out['rev_result'] = 'inline' if inline else 'above' if delta > 0 else 'below'
        else: out['revenue_comparison_note'] = 'Provider actual and consolidated release revenue have different bases; surprise omitted.'
    observation = row.get('consensus_eps_observation') or {}
    adjusted = record['eps_act'] != record['gaap_eps']
    out['eps_adjusted'] = adjusted
    supported = (observation.get('source_url') == row.get('source_url') and observation.get('period_end') == row.get('period_end')
                 and observation.get('current_actual') == record['eps_act'] and bool(observation.get('source_quote')))
    if (record['eps_est'] is not None and record['eps_est'] > 0 and record['eps_act'] is not None
            and (supported if adjusted else eps.get('current') == record['eps_act'])):
        if supported: out['eps_basis'] = observation['basis']
        out['eps_actual_used'] = record['eps_act']
        out['eps_surprise'] = float((Decimal(str(record['eps_act'])) - Decimal(str(record['eps_est']))) / abs(Decimal(str(record['eps_est']))))
    else: out['eps_comparison_note'] = 'Consensus-comparable EPS actual lacks a matching reviewed release quote; surprise omitted.'
    out['market_note'] = (('Revenue surprise uses ' + out.get('rev_basis', '') + '; growth uses consolidated revenue. ' if row.get('consensus_revenue') else '') + 'Consensus: MarketBeat, ' + record['quarter'] + ' (' + record['date'] + '). '
                          'EPS surprise basis: ' + out['eps_basis'] + '. EPS growth follows the labelled GAAP basis. '
                          'Revenue inside the published estimate’s rounding interval is shown as inline.')
    return out
