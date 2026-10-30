/**
 * The design system's public surface.
 *
 * Import from '@/ui' (or '../ui') — never reach into individual files, so the
 * internal structure stays free to change.
 */
import './ui.css'

export { Button, type ButtonProps, type ButtonVariant, type ButtonSize } from './Button'
export { LoadingMark } from './LoadingMark'
export { PdfLoading } from './PdfLoading'
export { IconButton, type IconButtonProps } from './IconButton'
export { Badge, type BadgeTone } from './Badge'
export { Input, Textarea, Select, Checkbox, FieldShell } from './Field'
export { useAmountField, groupAmount, parseAmount } from './useAmountField'
export { Switch } from './Switch'
export { Dropdown, type DropdownOption } from './Dropdown'
export { Pager, usePagedRows, useInfiniteRows, LoadMore, pageItems, pageCount, PAGE_SIZES } from './Pager'
export { Modal, type ModalProps } from './Modal'
export { ConfirmDialog, type ConfirmDialogProps } from './ConfirmDialog'
export { ToastProvider, useToast, type ToastTone } from './Toast'
export { Skeleton, SkeletonRows, EmptyState, ErrorState } from './States'
export { DataTable, type Column, type SortDir } from './DataTable'
export { Card, CardHeader, CardBody, PageHeader, Tooltip, FilterChip } from './Layout'
export {
  useColumnFilters,
  FilterTh,
  FilterStatus,
  type ColumnFilterState,
  type SortState,
} from './ColumnFilter'
export {
  applyColumnFilters,
  optionsFor,
  describe as describeCondition,
  isActive as isConditionActive,
  emptyCondition,
  activeCount,
  TEXT_OPS,
  type FilterField,
  type FilterKind,
  type Condition,
  type ConditionMap,
  type TextOp,
} from './columnFilters'
export { useFocusTrap, useScrollLock } from './useFocusTrap'
export {
  formatMoney,
  formatMoneyCompact,
  formatDate,
  formatDateTime,
  formatRelativeDays,
  currencySymbol,
  humanise,
  DEFAULT_CURRENCY,
  DEFAULT_LOCALE,
  setDisplayFormat,
  displayCurrency,
  displayLocale,
  localeForCurrency,
} from './format'
