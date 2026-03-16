import { Injectable, inject, signal } from '@angular/core';
import { AlertsApiClient } from '../../infrastructure/api-clients/alerts-api.client';
import { NotificationService } from '../../shared/services/notification.service';
import { AlertConfig, AlertConfigCreate } from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class AlertsFacade {
  private readonly api = inject(AlertsApiClient);
  private readonly notifications = inject(NotificationService);

  readonly config = signal<AlertConfig | null>(null);
  readonly loading = signal(false);
  readonly saving = signal(false);

  loadConfig(userId: string): void {
    this.loading.set(true);
    this.api.getByUserId(userId).subscribe({
      next: (data) => {
        this.config.set(data);
        this.loading.set(false);
      },
      error: () => {
        this.config.set(null);
        this.loading.set(false);
      },
    });
  }

  saveConfig(config: AlertConfigCreate): void {
    this.saving.set(true);
    this.api.createOrUpdate(config).subscribe({
      next: (data) => {
        this.config.set(data);
        this.saving.set(false);
        this.notifications.success('Configuracion de alertas guardada');
      },
      error: () => this.saving.set(false),
    });
  }

  deleteConfig(userId: string): void {
    this.saving.set(true);
    this.api.deleteByUserId(userId).subscribe({
      next: () => {
        this.config.set(null);
        this.saving.set(false);
        this.notifications.info('Alertas desactivadas');
      },
      error: () => this.saving.set(false),
    });
  }
}
