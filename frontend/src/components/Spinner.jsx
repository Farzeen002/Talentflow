const Spinner = ({ size = 6 }) => (
  <div
    className={`animate-spin rounded-full border-2 border-slate-300 border-t-slate-900 h-${size} w-${size}`}
    style={{ height: `${size * 4}px`, width: `${size * 4}px` }}
  />
);

export default Spinner;
