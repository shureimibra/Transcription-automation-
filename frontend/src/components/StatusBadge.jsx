import React from 'react';

// The single definition of what a task status looks like.
//
// STATUS ALWAYS COMES FROM THE SERVER. This module maps a backend status
// string to a colour and a label; it never derives, guesses or optimistically
// advances one. If the server sends a status this map does not know, the raw
// value is shown rather than a wrong friendly label -- being visibly unknown
// is safer than being confidently wrong about the state of someone's work.
const STATUS = {
  new:             { label: 'New',             dot: 'bg-status-new',       text: 'text-status-new',       ring: 'ring-status-new/30' },
  assigned:        { label: 'Assigned',        dot: 'bg-status-assigned',  text: 'text-status-assigned',  ring: 'ring-status-assigned/30' },
  claimed:         { label: 'Claimed',         dot: 'bg-status-assigned',  text: 'text-status-assigned',  ring: 'ring-status-assigned/30' },
  in_progress:     { label: 'In progress',     dot: 'bg-status-progress',  text: 'text-status-progress',  ring: 'ring-status-progress/30' },
  review:          { label: 'In review',       dot: 'bg-status-review',    text: 'text-status-review',    ring: 'ring-status-review/30' },
  ready_to_submit: { label: 'Ready to submit', dot: 'bg-status-ready',     text: 'text-status-ready',     ring: 'ring-status-ready/30' },
  submitted:       { label: 'Awaiting review',      dot: 'bg-status-submitted', text: 'text-status-submitted', ring: 'ring-status-submitted/30' },
  approved:        { label: 'Approved',        dot: 'bg-status-approved',  text: 'text-status-approved',  ring: 'ring-status-approved/30' },
  rejected:        { label: 'Rejected',        dot: 'bg-status-rejected',  text: 'text-status-rejected',  ring: 'ring-status-rejected/30' },
  failed:          { label: 'Failed',          dot: 'bg-status-failed',    text: 'text-status-failed',    ring: 'ring-status-failed/30' },
  cancelled:       { label: 'Cancelled',       dot: 'bg-status-cancelled', text: 'text-status-cancelled', ring: 'ring-status-cancelled/30' },
};

export function statusMeta(status) {
  return STATUS[status] || {
    label: String(status || 'unknown').replace(/_/g, ' '),
    dot: 'bg-ink-mute', text: 'text-ink-mute', ring: 'ring-ink-mute/30',
  };
}

export const STATUS_ORDER = [
  'new', 'assigned', 'claimed', 'in_progress', 'review',
  'ready_to_submit', 'submitted', 'approved', 'rejected', 'failed', 'cancelled',
];

/**
 * A status pill.
 *
 * The dot carries the colour and the word carries the meaning, so the badge
 * stays readable for anyone who cannot distinguish the hues -- colour is
 * never the only channel.
 */
export default function StatusBadge({ status, size = 'md', className = '' }) {
  const meta = statusMeta(status);
  const scale = size === 'sm'
    ? 'text-2xs px-1.5 py-0.5 gap-1'
    : 'text-xs px-2 py-1 gap-1.5';
  return (
    <span
      className={`inline-flex items-center ${scale} rounded-md ring-1 ${meta.ring} ${meta.text} bg-overlay/60 font-medium whitespace-nowrap ${className}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} aria-hidden="true" />
      {meta.label}
    </span>
  );
}
