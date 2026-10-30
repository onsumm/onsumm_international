import { Building2, Store, FileText, Mail, Sparkles } from 'lucide-react'
import type { NodeType } from '../../api/factGraph'

export interface NodeMeta {
  color: string   // ring + accent
  bg: string      // fill
  icon: React.ElementType
  radius: number  // base canvas radius (graph units)
  label: string   // legend label
}

// On-brand palette, consistent with the rest of the app (indigo/blue/green/amber/gray).
export const NODE_META: Record<NodeType, NodeMeta> = {
  tenant:  { color: '#4f46e5', bg: '#eef2ff', icon: Building2, radius: 11, label: 'Company' },
  vendor:  { color: '#2563eb', bg: '#eff6ff', icon: Store,     radius: 8,  label: 'Vendor' },
  bill:    { color: '#059669', bg: '#ecfdf5', icon: FileText,  radius: 6,  label: 'Bill' },
  contact: { color: '#d97706', bg: '#fffbeb', icon: Mail,      radius: 5,  label: 'Contact' },
  fact:    { color: '#6b7280', bg: '#f9fafb', icon: Sparkles,  radius: 4,  label: 'Fact' },
}

export const NODE_ORDER: NodeType[] = ['tenant', 'vendor', 'bill', 'contact', 'fact']

export const REL_LABELS: Record<string, string> = {
  owns: 'owns',
  has_bill: 'has bill',
  poc_of: 'contact of',
  constrains: 'constrains',
  about: 'about',
}
