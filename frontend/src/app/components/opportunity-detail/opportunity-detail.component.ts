import { Component, inject } from '@angular/core';
import { DecimalPipe, DatePipe } from '@angular/common';
import { OpportunityService } from '../../services/opportunity.service';

@Component({
  selector: 'app-opportunity-detail',
  standalone: true,
  imports: [DecimalPipe, DatePipe],
  template: `
    <!-- Backdrop -->
    <div class="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" (click)="close()"></div>

    <!-- Panel -->
    <div class="fixed inset-y-0 right-0 z-50 w-full max-w-2xl bg-surface-900 border-l border-surface-600 shadow-2xl
                overflow-y-auto animate-slide-in">

      @if (svc.loadingDetail()) {
        <div class="flex items-center justify-center h-full">
          <div class="w-8 h-8 border-2 border-accent-blue/30 border-t-accent-blue rounded-full animate-spin"></div>
        </div>
      }

      @if (detail(); as d) {
        <!-- Header -->
        <div class="sticky top-0 bg-surface-900/95 backdrop-blur border-b border-surface-600 p-5 flex items-start gap-4">
          <div class="w-24 h-24 rounded-xl bg-surface-700 flex-shrink-0 overflow-hidden">
            @if (d.image_url) {
              <img [src]="d.image_url" [alt]="d.product_name" class="w-full h-full object-cover" />
            } @else {
              <div class="w-full h-full flex items-center justify-center text-gray-600 text-3xl">?</div>
            }
          </div>
          <div class="flex-1 min-w-0">
            <h2 class="text-lg font-bold text-white leading-tight">{{ d.product_name }}</h2>
            <div class="flex flex-wrap gap-2 mt-2">
              @if (d.brand) {
                <span class="badge bg-accent-blue/20 text-accent-blue">{{ d.brand }}</span>
              }
              @if (d.category) {
                <span class="badge bg-surface-600 text-gray-300">{{ d.category }}</span>
              }
              <span class="badge bg-surface-600 text-gray-400">{{ d.created_at | date:'medium' }}</span>
            </div>
          </div>
          <button (click)="close()"
                  class="text-gray-500 hover:text-white transition-colors text-2xl leading-none p-1">
            &times;
          </button>
        </div>

        <div class="p-5 space-y-6">
          <!-- Arbitraje resumen -->
          <div class="grid grid-cols-3 gap-3">
            <div class="bg-surface-800 rounded-xl p-4 text-center border border-surface-600">
              <p class="text-[10px] text-gray-500 uppercase tracking-wider">Compra</p>
              <p class="text-xl font-bold text-white mt-1">\${{ d.buy_price | number:'1.2-2' }}</p>
              <p class="text-xs text-gray-500 mt-1">{{ d.buy_marketplace }}</p>
            </div>
            <div class="bg-surface-800 rounded-xl p-4 text-center border border-surface-600">
              <p class="text-[10px] text-gray-500 uppercase tracking-wider">Venta</p>
              <p class="text-xl font-bold text-white mt-1">\${{ d.sell_price | number:'1.2-2' }}</p>
              <p class="text-xs text-gray-500 mt-1">{{ d.sell_marketplace }}</p>
            </div>
            <div class="bg-surface-800 rounded-xl p-4 text-center border border-accent-green/30">
              <p class="text-[10px] text-gray-500 uppercase tracking-wider">Profit</p>
              <p class="text-xl font-bold text-accent-green mt-1">\${{ d.net_profit | number:'1.2-2' }}</p>
              <p class="text-xs font-semibold mt-1" [class]="roiColor">
                ROI {{ d.roi * 100 | number:'1.1-1' }}%
              </p>
            </div>
          </div>

          <!-- Desglose de costos -->
          <div class="bg-surface-800 rounded-xl border border-surface-600 p-4">
            <h3 class="text-sm font-semibold text-white mb-3">Desglose de costos</h3>
            <div class="space-y-2 text-sm">
              <div class="flex justify-between">
                <span class="text-gray-400">Precio de compra</span>
                <span class="text-white">\${{ d.buy_price | number:'1.2-2' }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-gray-400">Comisiones marketplace</span>
                <span class="text-accent-red">-\${{ d.fees | number:'1.2-2' }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-gray-400">Costo de envio</span>
                <span class="text-accent-red">-\${{ d.shipping_cost | number:'1.2-2' }}</span>
              </div>
              <div class="border-t border-surface-600 pt-2 flex justify-between font-semibold">
                <span class="text-gray-300">Ganancia neta</span>
                <span class="text-accent-green">\${{ d.net_profit | number:'1.2-2' }}</span>
              </div>
            </div>
          </div>

          <!-- Links de accion -->
          <div class="flex gap-3">
            <a [href]="d.buy_url" target="_blank" rel="noopener"
               class="btn-primary flex-1 text-center">
              Comprar en {{ d.buy_marketplace }}
            </a>
            <a [href]="d.sell_url" target="_blank" rel="noopener"
               class="flex-1 text-center py-2 px-4 bg-surface-600 hover:bg-surface-700 text-gray-300 rounded-lg transition-colors font-medium">
              Ver en {{ d.sell_marketplace }}
            </a>
          </div>

          <!-- Listings relacionados -->
          @if (d.related_listings.length > 0) {
            <div>
              <h3 class="text-sm font-semibold text-white mb-3">
                Listings del mismo producto
                <span class="text-gray-500 font-normal ml-1">({{ d.related_listings.length }})</span>
              </h3>
              <div class="space-y-2">
                @for (listing of d.related_listings; track listing.id) {
                  <a [href]="listing.url" target="_blank" rel="noopener"
                     class="flex items-center gap-3 bg-surface-800 rounded-lg border border-surface-600 p-3
                            hover:border-accent-blue/40 transition-colors">
                    <div class="w-10 h-10 rounded bg-surface-700 flex-shrink-0 overflow-hidden">
                      @if (listing.image_url) {
                        <img [src]="listing.image_url" class="w-full h-full object-cover" />
                      } @else {
                        <div class="w-full h-full flex items-center justify-center text-gray-600 text-xs">?</div>
                      }
                    </div>
                    <div class="flex-1 min-w-0">
                      <p class="text-xs text-gray-300 truncate">{{ listing.title }}</p>
                      <div class="flex items-center gap-2 mt-0.5">
                        <span class="text-[10px] text-gray-500">{{ listing.marketplace_id }}</span>
                        @if (listing.similarity_score) {
                          <span class="text-[10px] text-gray-600">sim: {{ listing.similarity_score | number:'1.2-2' }}</span>
                        }
                      </div>
                    </div>
                    <div class="text-right flex-shrink-0">
                      <p class="text-sm font-bold text-white">\${{ listing.price | number:'1.2-2' }}</p>
                      <p class="text-[10px] text-gray-500">{{ listing.currency }}</p>
                    </div>
                  </a>
                }
              </div>
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    @keyframes slideIn {
      from { transform: translateX(100%); }
      to { transform: translateX(0); }
    }
    .animate-slide-in {
      animation: slideIn 0.25s ease-out;
    }
  `],
})
export class OpportunityDetailComponent {
  svc = inject(OpportunityService);
  detail = this.svc.selectedOpportunity;

  get roiColor(): string {
    const d = this.detail();
    if (!d) return '';
    if (d.roi >= 0.5) return 'text-accent-green';
    if (d.roi >= 0.25) return 'text-accent-yellow';
    return 'text-accent-red';
  }

  close(): void {
    this.svc.clearSelection();
  }
}
