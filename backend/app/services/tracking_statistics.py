"""Statistics over evidence-backed completed entries, with explicit sample counts."""
import math
from app.services.tracking_costs import net_r


def summarize(rows, cost_bps):
    def evidence(row): return row.get('tracking_evidence') or {}
    entered = [r for r in rows if evidence(r).get('validated_entry_at')]
    resolved = [r for r in entered if evidence(r).get('category') == 'resolved'
                and evidence(r).get('remaining_quantity') == 0 and r.get('closed_at')
                and r.get('pnl_r_multiple') is not None and math.isfinite(float(r['pnl_r_multiple']))]
    scored = [(r, net_r(r, cost_bps)) for r in resolved]
    scored = [(r, value) for r, value in scored if value is not None and math.isfinite(value)]
    winners = [(r, value) for r, value in scored if value > 0]
    full = [r for r, _ in winners if r.get('tp2_hit_at')]
    partial = [r for r, _ in winners if r.get('tp1_hit_at') and not r.get('tp2_hit_at')]
    other = [r for r, _ in winners if not r.get('tp1_hit_at') and not r.get('tp2_hit_at')]
    gross = [float(r['pnl_r_multiple']) for r in resolved]
    net = [v for _, v in scored]
    candles = [int(r['candles_to_resolution']) for r in resolved if r.get('candles_to_resolution') is not None]
    no_entry = [r for r in rows if r.get('result') == 'NO_ENTRY' and evidence(r).get('category') == 'unentered']
    unresolved = [r for r in rows if r.get('result') in {'UNRESOLVED','AMBIGUOUS'} or not evidence(r)]
    return dict(total_ideas=len(rows), entry_triggered_count=len(entered),
        win_count=len(full), full_win_count=len(full), partial_win_count=len(partial), time_exit_win_count=len(other),
        loss_count=sum(v < 0 for v in net), break_even_count=sum(v == 0 for v in net),
        no_entry_count=len(no_entry), ambiguous_count=sum(r.get('status') == 'AMBIGUOUS' for r in rows),
        unresolved_count=len(unresolved), legacy_unreplayed_count=sum(not evidence(r) for r in rows),
        entered_unresolved_count=len(entered)-len(resolved), resolved_entered_count=len(resolved),
        open_count=sum(r.get('result') == 'OPEN' for r in rows),
        tp_hit_rate=round(100*sum(bool(r.get('tp1_hit_at')) for r in entered)/len(entered),2) if entered else 0,
        sl_hit_rate=round(100*sum(bool(r.get('sl_hit_at')) for r in entered)/len(entered),2) if entered else 0,
        win_rate=round(100*len(winners)/len(scored),2) if scored else 0,
        average_r_multiple=round(sum(gross)/len(gross),2) if gross else 0,
        average_candles_to_resolution=round(sum(candles)/len(candles),2) if candles else 0,
        expectancy_per_trade=round(sum(net)/len(net),2) if net else 0,
        expectancy_sample_count=len(net), expectancy_basis='completed entered trades with known modeled costs',
        tp1_then_sl_count=sum(bool(r.get('tp1_hit_at') and r.get('sl_hit_at') and not r.get('tp2_hit_at')) for r in resolved),
        tp1_then_tp2_count=sum(bool(r.get('tp1_hit_at') and r.get('tp2_hit_at')) for r in resolved),
        tp1_then_expiry_count=sum(bool(r.get('tp1_hit_at') and r.get('status') == 'TIME_EXIT') for r in resolved),
        partial_loss_count=sum(v < 0 and bool(r.get('tp1_hit_at')) for r,v in scored),
        cost_bps_per_side=cost_bps, costed_trade_count=len(net), gross_r_total=round(sum(gross),2),
        net_r_total=round(sum(net),2), average_net_r_multiple=round(sum(net)/len(net),2) if net else 0)


def grouped(rows, field, cost_bps):
    buckets = {}
    for row in rows: buckets.setdefault(row.get(field) or 'Unknown',[]).append(row)
    result = []
    for key, items in buckets.items():
        stats = summarize(items, cost_bps)
        result.append({field:key, 'count':len(items), 'entered_count':stats['entry_triggered_count'],
            'sample_count':stats['expectancy_sample_count'], 'win_rate':stats['win_rate'],
            'average_r':stats['average_net_r_multiple'], 'expectancy':stats['expectancy_per_trade'],
            'full_win_count':stats['full_win_count'], 'partial_win_count':stats['partial_win_count']})
    return sorted(result,key=lambda x:(x['expectancy'],x['sample_count']),reverse=True)[:5]
