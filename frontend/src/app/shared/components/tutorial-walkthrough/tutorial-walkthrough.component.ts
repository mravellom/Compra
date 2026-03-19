import { Component, signal, computed } from '@angular/core';

interface TutorialStep {
  title: string;
  content: string;
  warning?: string;
  pro_tip?: string;
}

const STEPS: TutorialStep[] = [
  {
    title: 'El Pipeline Completo',
    content: `Este sistema NO es un simple comparador de precios. Es un pipeline de 6 capas:

<strong>1. Scrapers</strong> — recolectan listings de Amazon, MercadoLibre, eBay cada ~48h
<strong>2. Processor</strong> — normaliza títulos y agrupa productos vía embeddings (pgvector)
<strong>3. Opportunity Engine v3</strong> — detecta pares comprar→vender entre marketplaces con TODOS los costos reales
<strong>4. Scoring System v3</strong> — produce 3 scores: opportunity (0-100), risk (0-100), confidence (0-100)
<strong>5. Arbitrage Validator</strong> — 7 checks anti-falso-positivo (freshness, spread, depth, worst-case, simulación)
<strong>6. Capital Manager</strong> — rankea, dimensiona posiciones y aplica disciplina de ejecución

Si una oportunidad llega a tu pantalla, ya pasó los filtros duros. Pero <strong>pasar filtros no significa que sea buena</strong> — fíjate en los scores.`,
    warning: 'El sistema usa soft-filters: la mayoría de checks son penalizaciones al score, NO rechazos duros. Que algo aparezca no significa que sea seguro.',
  },
  {
    title: 'Cada Fila = Un Par de Arbitrage',
    content: `Cada oportunidad es un par específico: <strong>comprar en marketplace A → vender en marketplace B</strong>.

<strong>Buy Price:</strong> el listing más barato del buy-side
<strong>Sell Price:</strong> mediana del sell-side × 0.97 (3% undercut)
<strong>Net Profit:</strong> sell - buy - TODOS los costos (comisión, payment processing, IVA, import tax, shipping)
<strong>Ruta:</strong> ej. US→MX, CN→CL — solo se evalúan rutas válidas predefinidas

El sistema verifica que buy y sell sean del MISMO producto (condición, variante, modelo, almacenamiento). Si ves un mismatch, reporta.`,
    warning: 'El sell price NO es el precio más alto — es la mediana con 3% undercut. Es el precio al que razonablemente podrías vender.',
  },
  {
    title: 'Profit, ROI y Confidence',
    content: `Estos tres números se leen JUNTOS. Uno solo no dice nada.

<strong>Net Profit:</strong> Ganancia neta después de TODOS los costos. Mínimo hard-filter: $1.
<strong>ROI:</strong> Profit / Buy Price. ROI > 200% recibe penalización. ROI > 500% = hard-reject.
<strong>Confidence:</strong> high (≥60), medium (≥40), low (<40).
  • 40% data coverage (ratings, reviews, sales, stability)
  • 35% signal agreement (factores apuntan al mismo lado)
  • 25% recency (listings frescos)

<strong>Patrones clave:</strong>
• Profit alto + Confidence low = <span style="color:#ef4444">TRAMPA</span>. Datos insuficientes.
• ROI > 100% + Confidence medium = probablemente error de matching.
• Profit bajo ($5-15) + Confidence high = oportunidad real para volumen.`,
    warning: 'Si ignoras el confidence level y operas solo por profit, vas a perder dinero. Es la métrica que más protege de falsos positivos.',
    pro_tip: 'El 80% de las oportunidades con profit > $100 y confidence low terminan siendo falsos positivos.',
  },
  {
    title: 'Risk Score: Lo Que Puede Salir Mal',
    content: `Risk score (0-100, menor = más seguro). 5 componentes:

<strong>Volatility Risk (25%)</strong> — 100 - stability. Precios inestables = peligro.
<strong>Competition Risk (20%)</strong> — 10+ competidores con spread < 5% = guerra de precios (85/100 risk).
<strong>Liquidity Risk (20%)</strong> — Inverso de demanda. Sin compradores, tu listing se queda colgado.
<strong>Margin Risk (15%)</strong> — Margen < 5% = risk 70. Margen > 25% = risk 25.
<strong>Cross-Border Risk (20%)</strong> — Doméstico = 10. Ruta fácil = 30. Media = 50. Difícil = 70.

<strong>Penalizaciones extra:</strong>
• ROI > 200%: +10 al risk
• Seller rating < 3.0: +5 por cada lado`,
    warning: 'Risk > 60 con Confidence < 50 = no operes. Risk 30-50 es zona normal para cross-border.',
  },
  {
    title: 'Execution Realism',
    content: `El sistema simula QUÉ PASARÍA si ejecutaras con delay realista:

<strong>Execution Simulation:</strong>
• Asume 3 minutos de delay detección → ejecución
• Aplica price decay de 0.5%/hora al sell price
• Si profit simulado ≤ $0 → RECHAZADO

<strong>Worst-Case Profit:</strong>
• Usa el percentil 25 (P25) del sell price, no la mediana
• Aplica 2% slippage de FX al buy price
• Aplica 5% buffer extra a todas las fees
• Si worst-case ≤ $0 → RECHAZADO

<strong>Price Freshness:</strong>
• Listings > 5 minutos → RECHAZADO`,
    pro_tip: 'En hardened mode, el engine aplica 30% haircut al profit y usa el precio MÍNIMO del competidor. Si pasa hardened mode, probablemente es real.',
    warning: 'El 40% de las oportunidades que parecen rentables a mediana dejan de serlo con P25 + slippage + fee buffer.',
  },
  {
    title: 'Arbitrage Validator: 7 Checks',
    content: `El Validator ejecuta 7 checks INDEPENDIENTES sin short-circuit:

<strong>1. Price Freshness</strong> — precios < 5 min
<strong>2. Spread Stability</strong> — CV de precios del sell-side < 0.25
<strong>3. Depth</strong> — ≥ 2 listings dentro del 5% del sell price, sales ≥ 3
<strong>4. Worst-Case Profit</strong> — rentable con P25 + 2% FX + 5% fees
<strong>5. Execution Simulation</strong> — rentable tras 3 min delay + 0.5%/h decay
<strong>6. Duplicate Suppression</strong> — dedup por hash en ventana de 10 min
<strong>7. Confidence Re-scoring</strong> — composite ≥ 0.30

El campo <strong>rejection_reasons</strong> te dice EXACTAMENTE por qué falló.`,
    warning: 'Sin este layer, el 30-50% de las oportunidades serían falsos positivos.',
  },
  {
    title: 'Capital Manager',
    content: `Decide CUÁNTO capital asignar y SI puedes ejecutar AHORA.

<strong>Ranking:</strong> confidence × profit × (1 - risk/100)
<strong>Top-K:</strong> Solo las top 5 oportunidades por ciclo pasan a evaluación.

<strong>5 Gates secuenciales:</strong>
1. <strong>Concurrent Limit:</strong> Máximo 10 trades abiertos
2. <strong>Cooldowns:</strong> 10 min por producto, 2 min por marketplace
3. <strong>Confidence Scaling:</strong> Confidence < 80% → position size reducido (mínimo 50%)
4. <strong>Execution Guard:</strong> Risk + sizing + diversificación
5. <strong>Commit:</strong> Solo en ejecución real

<strong>Stop Conditions automáticas:</strong>
• Max Drawdown 15% → HALT
• Daily Loss 5% del capital → HALT
• Win Rate < 50% después de 10 trades → HALT`,
    warning: 'NUNCA hagas resume() sin entender POR QUÉ se detuvo el sistema.',
    pro_tip: 'Si ves "product X in cooldown (540s remaining)" es NORMAL. Protege contra sobre-exposición.',
  },
  {
    title: 'Shadow Mode vs Real Execution',
    content: `El sistema tiene DOS niveles de shadow:

<strong>Validator Shadow:</strong> Logea qué habría rechazado, pero deja pasar. Para A/B testing.

<strong>Capital Manager Dry-Run:</strong> Simula sizing y allocation sin comprometer capital.

<strong>Shadow Execution Tracker:</strong> Re-valida TODAS las oportunidades tras 120s con precios frescos. Mide:
• <strong>Profit Drift:</strong> divergencia real vs predicho
• <strong>Success Rate:</strong> % que sigue rentable después del delay
• <strong>False Positive Rate:</strong> % que habría sido pérdida
• <strong>Category Decay:</strong> velocidad de cambio de precios por categoría

El <strong>AutoCalibrator</strong> usa este feedback para auto-ajustar thresholds (EMA smoothing, max ±5%/ciclo, auto-freeze si success < 50%).`,
    warning: 'Operar sin haber corrido en shadow al menos una semana es imprudente.',
  },
  {
    title: 'Oportunidades a IGNORAR',
    content: `<span style="color:#ef4444">Patrones de FALSO POSITIVO:</span>

<strong>ROI > 150% + competidores < 3</strong>
Probablemente error de matching o listing abandonado.

<strong>Profit > $80 + confidence "low"</strong>
Datos insuficientes para validar esa ganancia.

<strong>Risk > 65 en cualquier contexto</strong>
Demasiadas señales de peligro.

<strong>Cross-border + margin < 8%</strong>
Las fees de importación y FX se comerán el margen.

<strong>Solo 1 listing en el sell-side</strong>
Sin price discovery real.`,
  },
  {
    title: 'Oportunidades a TOMAR',
    content: `<span style="color:#22c55e">Patrones GANADORES:</span>

<strong>Score > 65, Confidence "high", Risk < 40</strong>
La trifecta. Datos sólidos, señales alineadas.

<strong>Profit $15-50 + ROI 15-40% + 5 competidores</strong>
Margen saludable en mercado líquido.

<strong>Doméstico + stability > 70</strong>
Precios estables, sin riesgo FX ni aduanas.

<strong>Productos con 50+ reviews + sales > 0</strong>
Demanda comprobada.

<strong>Worst-case profit > $10</strong>
Incluso en escenario pesimista, ganas.`,
    pro_tip: 'Las mejores oportunidades suelen tener profit "moderado" ($20-60) con alta confianza. Las de profit enorme ($100+) casi siempre son errores.',
  },
  {
    title: 'Reglas de Oro',
    content: `<strong>1. Confidence > Profit.</strong> Siempre. $15 con confidence high > $80 con confidence low.

<strong>2. Lee el trío completo.</strong> Score + Risk + Confidence son tres perspectivas del mismo trade.

<strong>3. Respeta los cooldowns.</strong> La diversificación te salva de las rachas malas.

<strong>4. Shadow antes de live.</strong> Mínimo 7 días en dry_run. Si success_rate < 75%, no estás listo.

<strong>5. Los márgenes reales son 3-12%.</strong> Si ves 50%+ consistente, investiga.

<strong>6. El worst-case profit es tu número real.</strong> El net_profit es optimista.

<strong>7. Drawdown > 8% = revisa todo.</strong> No esperes al halt del 15%.

<strong>8. Este sistema es una herramienta, no un oráculo.</strong> Supervisa siempre.`,
    warning: 'NUNCA operes con capital real sin haber validado en shadow con 100+ oportunidades re-validadas y success_rate > 75%.',
  },
];

@Component({
  selector: 'app-tutorial-walkthrough',
  standalone: true,
  template: `
    @if (visible()) {
      <!-- Backdrop -->
      <div class="fixed inset-0 z-[100] bg-black/70 backdrop-blur-sm" (click)="close()"></div>

      <!-- Modal -->
      <div class="fixed inset-0 z-[101] flex items-center justify-center p-4 pointer-events-none">
        <div class="bg-surface-800 border border-surface-600 rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col pointer-events-auto">

          <!-- Header -->
          <div class="flex items-center justify-between px-6 py-4 border-b border-surface-600 flex-shrink-0">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 rounded-lg bg-accent-blue flex items-center justify-center text-sm font-bold text-white">?</div>
              <div>
                <h2 class="text-base font-bold text-white">Guía Operativa</h2>
                <p class="text-xs text-gray-500">Paso {{ currentStep() + 1 }} de {{ totalSteps }}</p>
              </div>
            </div>
            <button (click)="close()" class="text-gray-500 hover:text-white transition-colors text-2xl leading-none">&times;</button>
          </div>

          <!-- Progress bar -->
          <div class="h-1 bg-surface-700 flex-shrink-0">
            <div class="h-full bg-accent-blue transition-all duration-300 rounded-r" [style.width.%]="progressPct()"></div>
          </div>

          <!-- Content (scrollable) -->
          <div class="flex-1 overflow-y-auto px-6 py-5 space-y-4">
            <h3 class="text-lg font-bold text-white">{{ step().title }}</h3>

            <div class="text-sm text-gray-300 leading-relaxed whitespace-pre-line tutorial-content" [innerHTML]="step().content"></div>

            @if (step().warning) {
              <div class="flex gap-3 bg-accent-red/10 border border-accent-red/20 rounded-lg p-3">
                <span class="text-accent-red text-lg flex-shrink-0">&#9888;</span>
                <p class="text-sm text-accent-red/90">{{ step().warning }}</p>
              </div>
            }

            @if (step().pro_tip) {
              <div class="flex gap-3 bg-accent-blue/10 border border-accent-blue/20 rounded-lg p-3">
                <span class="text-accent-blue text-lg flex-shrink-0">&#9733;</span>
                <p class="text-sm text-accent-blue/90">{{ step().pro_tip }}</p>
              </div>
            }
          </div>

          <!-- Footer navigation -->
          <div class="flex items-center justify-between px-6 py-4 border-t border-surface-600 flex-shrink-0">
            <button (click)="prev()"
                    [disabled]="currentStep() === 0"
                    class="px-4 py-2 text-sm rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed text-gray-400 hover:text-white hover:bg-surface-700">
              &larr; Anterior
            </button>

            <!-- Step dots -->
            <div class="flex gap-1.5">
              @for (s of steps; track $index) {
                <button (click)="currentStep.set($index)"
                        class="w-2 h-2 rounded-full transition-all"
                        [class]="$index === currentStep() ? 'bg-accent-blue w-4' : $index < currentStep() ? 'bg-accent-blue/40' : 'bg-surface-600'">
                </button>
              }
            </div>

            @if (currentStep() < totalSteps - 1) {
              <button (click)="next()"
                      class="px-4 py-2 text-sm rounded-lg bg-accent-blue hover:bg-accent-blue/80 text-white font-medium transition-colors">
                Siguiente &rarr;
              </button>
            } @else {
              <button (click)="close()"
                      class="px-4 py-2 text-sm rounded-lg bg-accent-green hover:bg-accent-green/80 text-white font-medium transition-colors">
                Entendido
              </button>
            }
          </div>
        </div>
      </div>
    }
  `,
  styles: [`
    :host ::ng-deep .tutorial-content strong {
      color: white;
    }
  `],
})
export class TutorialWalkthroughComponent {
  readonly visible = signal(false);
  readonly currentStep = signal(0);
  readonly steps = STEPS;
  readonly totalSteps = STEPS.length;

  readonly step = computed(() => STEPS[this.currentStep()]);
  readonly progressPct = computed(() => ((this.currentStep() + 1) / this.totalSteps) * 100);

  open(): void {
    this.currentStep.set(0);
    this.visible.set(true);
  }

  close(): void {
    this.visible.set(false);
  }

  next(): void {
    if (this.currentStep() < this.totalSteps - 1) {
      this.currentStep.update(s => s + 1);
    }
  }

  prev(): void {
    if (this.currentStep() > 0) {
      this.currentStep.update(s => s - 1);
    }
  }
}
