// 型態學股票分析 — 前端應用
(() => {
  const $ = (id) => document.getElementById(id);

  // ----- Tab 切換 -----
  document.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('.view').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      $(`view-${t.dataset.tab}`).classList.add('active');
    });
  });

  // ----- 市場切換 -----
  let currentMarket = 'auto';
  document.querySelectorAll('.seg').forEach(s => {
    s.addEventListener('click', () => {
      document.querySelectorAll('.seg').forEach(x => x.classList.remove('active'));
      s.classList.add('active');
      currentMarket = s.dataset.market;
    });
  });

  // ----- 常用快選 -----
  document.querySelectorAll('.chip').forEach(c => {
    c.addEventListener('click', () => {
      $('symbol').value = c.dataset.sym;
      // 設定 market segment
      const m = c.dataset.m || 'auto';
      document.querySelectorAll('.seg').forEach(x => {
        x.classList.toggle('active', x.dataset.market === m);
      });
      currentMarket = m;
      runAnalysis();
    });
  });

  // ----- Enter 觸發 -----
  $('symbol').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') runAnalysis();
  });
  $('go').addEventListener('click', runAnalysis);

  // ----- 圖表設定 -----
  let chart = null, candleSeries = null, volumeSeries = null;
  function initChart() {
    if (chart) return;
    chart = LightweightCharts.createChart($('chart'), {
      autoSize: true,
      layout: {
        background: { color: '#161b22' },
        textColor: '#e6edf3',
      },
      grid: {
        vertLines: { color: '#2d333b' },
        horzLines: { color: '#2d333b' },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#2d333b' },
      timeScale: {
        borderColor: '#2d333b',
        timeVisible: true,
        secondsVisible: false,
      },
    });
    candleSeries = chart.addCandlestickSeries({
      upColor: '#3fb950',
      downColor: '#f85149',
      borderUpColor: '#3fb950',
      borderDownColor: '#f85149',
      wickUpColor: '#3fb950',
      wickDownColor: '#f85149',
    });
    volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      color: '#586069',
    });
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });
  }

  // ----- 設定狀態文字 -----
  function setStatus(msg, isError = false) {
    const el = $('status');
    el.textContent = msg;
    el.classList.toggle('error', isError);
  }

  // ----- 主分析流程 -----
  async function runAnalysis() {
    const symbol = $('symbol').value.trim();
    if (!symbol) {
      setStatus('請輸入代號', true);
      return;
    }
    const range = $('range').value;
    setStatus(`抓取 ${symbol} (${range}) 中…`);
    $('go').disabled = true;
    try {
      const resp = await fetch(`/api/analyze?symbol=${encodeURIComponent(symbol)}&market=${currentMarket}&range=${range}`);
      const data = await resp.json();
      if (!resp.ok || data.error) {
        setStatus(`錯誤：${data.error || resp.status}`, true);
        return;
      }
      renderResults(data);
      setStatus(`✅ ${data.symbol} 分析完成 · ${data.candles.length} 根 K 線`);
    } catch (e) {
      setStatus(`網路錯誤：${e.message}`, true);
    } finally {
      $('go').disabled = false;
    }
  }

  // ----- 渲染結果 -----
  function renderResults(data) {
    $('results').classList.remove('hidden');
    initChart();

    // Header
    const name = data.longName || data.symbol;
    $('title').textContent = `${name} (${data.symbol})`;
    $('subtitle').textContent = `${data.exchangeName || ''} · ${data.currency || ''}`;
    const price = data.regularMarketPrice ?? data.candles[data.candles.length-1]?.close;
    const prev = data.previousClose;
    $('price').textContent = price?.toFixed(2) ?? '—';
    if (price && prev) {
      const diff = price - prev;
      const pct = (diff / prev) * 100;
      const sign = diff >= 0 ? '+' : '';
      $('change').textContent = `${sign}${diff.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
      $('change').classList.toggle('up', diff >= 0);
      $('change').classList.toggle('down', diff < 0);
    } else {
      $('change').textContent = '';
    }

    // 圖表
    const candles = data.candles.map(c => ({
      time: c.time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    const volumes = data.candles.map(c => ({
      time: c.time,
      value: c.volume || 0,
      color: c.close >= c.open ? 'rgba(63,185,80,0.4)' : 'rgba(248,81,73,0.4)',
    }));
    candleSeries.setData(candles);
    volumeSeries.setData(volumes);

    // 標記擺動點
    const pivotMarkers = (data.analysis?.pivots || []).map(p => ({
      time: p.time,
      position: p.kind === 'high' ? 'aboveBar' : 'belowBar',
      color: p.kind === 'high' ? '#f85149' : '#3fb950',
      shape: p.kind === 'high' ? 'arrowDown' : 'arrowUp',
      text: p.kind === 'high' ? 'H' : 'L',
    }));
    candleSeries.setMarkers(pivotMarkers);
    chart.timeScale().fitContent();

    // 分析區塊
    const a = data.analysis;
    if (!a || a.status !== 'ok') {
      $('trend').textContent = a?.message || '—';
      $('volprice').textContent = '—';
      $('patterns').textContent = '—';
      $('advice').textContent = '—';
      return;
    }

    // 趨勢
    const tCls = a.trend.direction === 'uptrend' ? 'uptrend'
              : a.trend.direction === 'downtrend' ? 'downtrend'
              : 'sideways';
    $('trend').innerHTML = `<span class="tag ${tCls}">${a.trend.label}</span>`;

    // 量價
    const vp = a.volume_analysis;
    $('volprice').innerHTML = `
      <div>${vp.vol_price_combo}</div>
      <div style="color:var(--muted);font-size:12px;margin-top:4px">
        近 5 日成交量 / 前 20 日均量 = ${vp.vol_ratio}x · 近 5 日漲跌 ${a.recent_change_pct}%
      </div>
    `;

    // 型態
    if (a.patterns.length === 0) {
      $('patterns').innerHTML = '<span style="color:var(--muted)">未偵測到明顯型態</span>';
    } else {
      $('patterns').innerHTML = a.patterns.map(p => {
        let extra = '';
        if (p.type === 'double_bottom') {
          extra = `第一底 ${p.first_bottom.price.toFixed(2)}, 第二底 ${p.second_bottom.price.toFixed(2)}`;
        } else if (p.type === 'double_top') {
          extra = `第一頂 ${p.first_top.price.toFixed(2)}, 第二頂 ${p.second_top.price.toFixed(2)}`;
        } else if (p.type === 'po_di_fan') {
          extra = `破底低點 ${p.breakdown_low.toFixed(2)}, 反彈 ${p.current_rebound_pct}%`;
        }
        return `<div><span class="tag pattern">${p.label}</span><div style="color:var(--muted);font-size:12px;margin:2px 0 8px 0">${extra}<br>${p.neckline_hint || p.note || ''}</div></div>`;
      }).join('');
    }

    // 建議
    $('advice').innerHTML = '<ul>' + a.advice.map(s => `<li>${s}</li>`).join('') + '</ul>';

    // 訊號燈 (明確的買/賣/觀望)
    if (a.signal) {
      const sig = a.signal;
      const sigEl = $('signal-block');
      if (sigEl) {
        const arrow = sig.action === 'BUY' ? '▲' : sig.action === 'SELL' ? '▼' : '●';
        const actionText = sig.action === 'BUY' ? '買進' : sig.action === 'SELL' ? '賣出' : sig.action === 'WATCH' ? '觀望' : '等待';
        sigEl.innerHTML = `
          <div class="signal-card" style="background:${sig.color}22;border:2px solid ${sig.color};border-radius:12px;padding:16px;margin-bottom:14px;text-align:center">
            <div style="font-size:42px;font-weight:900;color:${sig.color};line-height:1">${arrow} ${actionText}</div>
            <div style="font-size:18px;color:${sig.color};margin-top:6px;font-weight:600">${sig.level}</div>
            <div style="font-size:13px;color:var(--muted);margin-top:6px">綜合分數: <strong style="color:${sig.color}">${sig.score > 0 ? '+' : ''}${sig.score}</strong> / ±100</div>
            <details style="margin-top:8px;text-align:left">
              <summary style="cursor:pointer;color:var(--muted);font-size:12px">評分明細</summary>
              <ul style="font-size:12px;margin:6px 0 0 0;padding-left:20px;color:var(--muted)">
                ${sig.reasons.map(r => `<li>${r}</li>`).join('')}
              </ul>
            </details>
          </div>
        `;
      }
    }

    // 擺動點表格
    const tbody = $('pivots').querySelector('tbody');
    tbody.innerHTML = a.pivots.map(p => {
      const d = new Date(p.time * 1000);
      const ds = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
      return `<tr><td>${ds}</td><td class="kind-${p.kind}">${p.kind === 'high' ? '高點' : '低點'}</td><td>${p.price.toFixed(2)}</td></tr>`;
    }).join('');
  }

  // 自動載入台積電
  window.addEventListener('load', () => {
    $('symbol').value = '2330';
    setTimeout(runAnalysis, 300);
  });
})();
