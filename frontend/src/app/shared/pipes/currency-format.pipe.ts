import { Pipe, PipeTransform } from '@angular/core';

@Pipe({ name: 'currencyFormat', standalone: true })
export class CurrencyFormatPipe implements PipeTransform {
  transform(value: number | null | undefined, decimals = 2): string {
    if (value == null) return '—';
    return `$${value.toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}`;
  }
}
