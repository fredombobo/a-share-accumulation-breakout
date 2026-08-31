export function EmptyState({
  title = '暂无数据',
  hint,
  action,
}: {
  title?: string
  hint?: string
  action?: { label: string; onClick: () => void }
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center">
      <div className="text-sm font-medium text-slate-600">{title}</div>
      {hint && <div className="mt-1 max-w-md text-xs text-slate-500">{hint}</div>}
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="mt-3 rounded-md bg-slate-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700"
        >
          {action.label}
        </button>
      )}
    </div>
  )
}
