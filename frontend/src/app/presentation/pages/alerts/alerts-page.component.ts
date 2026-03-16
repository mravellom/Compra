import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AlertsFacade } from '../../../application/facades/alerts.facade';
import { LoadingSpinnerComponent } from '../../../shared/components/loading-spinner/loading-spinner.component';

const DEFAULT_USER_ID = 'default_user';

@Component({
  selector: 'app-alerts-page',
  standalone: true,
  imports: [FormsModule, LoadingSpinnerComponent],
  template: `
    <div class="p-6 space-y-6">
      <h1 class="text-xl font-bold text-white">Configuracion de Alertas</h1>

      @if (facade.loading()) { <app-loading-spinner /> }

      @if (!facade.loading()) {
        <div class="bg-surface-800 rounded-xl border border-surface-600 p-6 space-y-6 max-w-2xl">
          <!-- Enable/Disable -->
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-sm font-semibold text-white">Alertas Activas</h2>
              <p class="text-xs text-gray-500 mt-1">Recibe notificaciones cuando se detecten oportunidades</p>
            </div>
            <label class="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" [(ngModel)]="form.enabled" class="sr-only peer" />
              <div class="w-11 h-6 bg-surface-600 rounded-full peer peer-checked:bg-accent-blue transition-colors
                          after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full
                          after:h-5 after:w-5 after:transition-all peer-checked:after:translate-x-full"></div>
            </label>
          </div>

          <!-- Thresholds -->
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="text-xs text-gray-500 block mb-1">ROI Minimo</label>
              <input type="number" [(ngModel)]="form.min_roi" class="input-dark w-full" step="0.05" min="0" max="1" />
              <p class="text-[10px] text-gray-600 mt-1">ej: 0.20 = 20%</p>
            </div>
            <div>
              <label class="text-xs text-gray-500 block mb-1">Profit Minimo ($)</label>
              <input type="number" [(ngModel)]="form.min_profit" class="input-dark w-full" step="5" min="0" />
            </div>
            <div>
              <label class="text-xs text-gray-500 block mb-1">Precio Max. Compra ($)</label>
              <input type="number" [(ngModel)]="form.max_buy_price" class="input-dark w-full" step="50" />
            </div>
          </div>

          <!-- Notification Channels -->
          <div class="space-y-4">
            <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wider">Canales de Notificacion</h3>
            <div>
              <label class="text-xs text-gray-500 block mb-1">Telegram Chat ID</label>
              <input type="text" [(ngModel)]="form.telegram_chat_id" class="input-dark w-full" placeholder="123456789" />
            </div>
            <div>
              <label class="text-xs text-gray-500 block mb-1">Email</label>
              <input type="email" [(ngModel)]="form.email" class="input-dark w-full" placeholder="tu@email.com" />
            </div>
          </div>

          <!-- Actions -->
          <div class="flex gap-3 pt-4 border-t border-surface-600">
            <button (click)="save()" [disabled]="facade.saving()"
                    class="btn-primary flex items-center gap-2 disabled:opacity-50">
              @if (facade.saving()) {
                <span class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
              }
              Guardar
            </button>
            @if (facade.config()) {
              <button (click)="deleteConfig()"
                      class="px-4 py-2 bg-accent-red/20 text-accent-red rounded-lg hover:bg-accent-red/30 transition-colors">
                Eliminar
              </button>
            }
          </div>
        </div>

        <!-- Current Config Display -->
        @if (facade.config(); as c) {
          <div class="bg-surface-800 rounded-xl border border-surface-600 p-5 max-w-2xl">
            <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Configuracion Actual</h3>
            <div class="grid grid-cols-2 gap-3 text-sm">
              <div class="flex justify-between"><span class="text-gray-500">Estado</span><span [class]="c.enabled ? 'text-accent-green' : 'text-accent-red'">{{ c.enabled ? 'Activo' : 'Inactivo' }}</span></div>
              <div class="flex justify-between"><span class="text-gray-500">ROI Min</span><span class="text-white">{{ c.min_roi * 100 }}%</span></div>
              <div class="flex justify-between"><span class="text-gray-500">Profit Min</span><span class="text-white">\${{ c.min_profit }}</span></div>
              <div class="flex justify-between"><span class="text-gray-500">Telegram</span><span class="text-white">{{ c.telegram_chat_id || '—' }}</span></div>
              <div class="flex justify-between"><span class="text-gray-500">Email</span><span class="text-white">{{ c.email || '—' }}</span></div>
            </div>
          </div>
        }
      }
    </div>
  `,
})
export class AlertsPageComponent implements OnInit {
  readonly facade = inject(AlertsFacade);

  form = {
    enabled: true,
    min_roi: 0.20,
    min_profit: 30,
    max_buy_price: null as number | null,
    telegram_chat_id: '',
    email: '',
  };

  ngOnInit(): void {
    this.facade.loadConfig(DEFAULT_USER_ID);
  }

  save(): void {
    this.facade.saveConfig({
      user_id: DEFAULT_USER_ID,
      min_roi: this.form.min_roi,
      min_profit: this.form.min_profit,
      max_buy_price: this.form.max_buy_price,
      telegram_chat_id: this.form.telegram_chat_id || null,
      email: this.form.email || null,
      enabled: this.form.enabled,
    });
  }

  deleteConfig(): void {
    this.facade.deleteConfig(DEFAULT_USER_ID);
  }
}
