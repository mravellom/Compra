import { Injectable, signal } from '@angular/core';

export interface Notification {
  id: number;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
}

@Injectable({ providedIn: 'root' })
export class NotificationService {
  private nextId = 0;
  readonly notifications = signal<Notification[]>([]);

  success(message: string): void {
    this.add('success', message);
  }

  error(message: string): void {
    this.add('error', message);
  }

  warning(message: string): void {
    this.add('warning', message);
  }

  info(message: string): void {
    this.add('info', message);
  }

  dismiss(id: number): void {
    this.notifications.update((list) => list.filter((n) => n.id !== id));
  }

  private add(type: Notification['type'], message: string): void {
    const id = this.nextId++;
    this.notifications.update((list) => [...list, { id, type, message }]);
    setTimeout(() => this.dismiss(id), 5000);
  }
}
