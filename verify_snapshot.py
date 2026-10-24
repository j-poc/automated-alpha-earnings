"""Independent replay of the saved snapshot's market comparisons from retained inputs."""
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo('America/New_York')

def verify():
    saved = json.loads((ROOT/'data/season.json').read_text())
    proofs = json.loads((ROOT/'data/market-proof.json').read_text())
    assert proofs['generated_at'] == saved['generated_at'], 'Source evidence belongs to another snapshot'
    asof = datetime.fromisoformat(saved['generated_at']).astimezone(ET)
    seen=set(); returns=eps_count=rev_count=0
    for row in saved['companies']:
        ticker=row['ticker']; assert ticker not in seen;seen.add(ticker)
        mk=row['market']; proof=proofs['companies'].get(ticker)
        if not proof:
            assert not mk.get("price") and not mk.get("reaction_history"), (ticker, "market values have no retained inputs")
            continue
        prices = {datetime.fromtimestamp(t,timezone.utc).astimezone(ET).date().isoformat(): c
                  for t,c in proof['closes'] if c is not None}
        dates=sorted(prices)
        for window in mk.get('reaction_history',[]):
            report=window['reported']; hour=window['hour']
            first=next((i for i,d in enumerate(dates) if d>=report and (hour=='bmo' or d>report)),None)
            assert first is not None and first>0,(ticker,report,'missing baseline')
            start,end=dates[first-1],dates[first+1]
            assert (start,end)==(window['baseline_date'],window['end_date']),(ticker,report,'wrong sessions')
            assert end < asof.date().isoformat() or end == asof.date().isoformat() and asof.hour >=16
            expected=(Decimal(str(prices[end]))/Decimal(str(prices[start]))-1)*100
            assert abs(expected-Decimal(str(window['r2'])))<=Decimal('.050001'),(ticker,report,'return')
            if window.get('r5') is not None:
                fifth=dates[first+4]
                assert fifth==window['r5_end_date']
                expected5=(Decimal(str(prices[fifth]))/Decimal(str(prices[start]))-1)*100
                assert abs(expected5-Decimal(str(window['r5'])))<=Decimal('.050001')
            returns+=1
        c=mk.get('consensus')
        if mk.get('rev_surprise') is not None:
            assert c and 0 <= (datetime.fromisoformat(row['filed'])-datetime.fromisoformat(c['date'])).days <=3
            actual,estimate=Decimal(str(mk['rev_actual_used'])),Decimal(str(c['rev_est']))
            expected=Decimal(0) if abs(actual-estimate)<=Decimal(str(c['rev_est_half_rounding'])) else actual/estimate-1
            assert abs(expected-Decimal(str(mk['rev_surprise'])))<Decimal('1e-14')
            assert (mk['rev_result']=='inline')==(expected==0);rev_count+=1
        if mk.get('eps_surprise') is not None:
            assert c and mk['eps_actual_used']==c['eps_act']
            if c['eps_act']!=c['gaap_eps']:
                review=row['consensus_eps_observation']
                assert review['source_url']==row['source_url'] and review['period_end']==row['period_end'] and review['current_actual']==c['eps_act']
            expected=Decimal(str(c['eps_act']))/Decimal(str(c['eps_est']))-1
            assert abs(expected-Decimal(str(mk['eps_surprise'])))<Decimal('1e-14');eps_count+=1
        o=proof.get('option') or {}
        if o.get('call') and o.get('put') and mk.get('implied') and not row.get('filed'):
            spot=Decimal(str(o['spot']));strike=o['strike']
            assert strike==min(o['strikes'],key=lambda x:abs(Decimal(str(x))-spot))
            def mid(leg):
                return (Decimal(str(leg['bid']))+Decimal(str(leg['ask'])))/2 if leg.get('bid') and leg.get('ask') else Decimal(str(leg['lastPrice']))
            move=(mid(o['call'])+mid(o['put']))/spot*100
            assert abs(move-Decimal(str(mk['implied']['move_pct'])))<=Decimal('.050001')
    print(f'Independent replay: {returns} historical return windows; {rev_count} revenue and {eps_count} EPS comparisons; option quote arithmetic checked')
    return True

if __name__=='__main__': verify()
