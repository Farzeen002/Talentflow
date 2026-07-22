import { useState, useEffect } from "react";
import { X, Download, Loader2, AlertCircle } from "lucide-react";
import { getResumePreview, getResumeDownload } from "../services/candidates";
import { toast } from "sonner";

const ResumeModal = ({
  isOpen,
  onClose,
  candidateId,
  candidateName,
  resumeStatus,
  /** When set (mock / centralized), skip API and load this URL in the iframe */
  mockResumeUrl = null,
}) => {
  const [resumeUrl, setResumeUrl] = useState(null);
  const [loading, setLoading] = useState(false);
  const [downloadLoading, setDownloadLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!isOpen) {
      setResumeUrl(null);
      setError(null);
      return;
    }
    if (mockResumeUrl) {
      setResumeUrl(mockResumeUrl);
      setLoading(false);
      setError(null);
      return;
    }
    if (candidateId && resumeStatus === "completed") {
      fetchResumePreview();
    }
  }, [isOpen, candidateId, resumeStatus, mockResumeUrl]);

  const fetchResumePreview = async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await getResumePreview(candidateId);
      setResumeUrl(data.url);
    } catch (err) {
      console.error("Failed to fetch resume preview:", err);
      setError("Failed to load resume. Please try again.");
      toast.error("Failed to load resume");
    } finally {
      setLoading(false);
    }
  };

  const handleDownloadResume = async () => {
    if (mockResumeUrl) {
      const link = document.createElement("a");
      link.href = mockResumeUrl;
      link.download = `${candidateName || "resume"}.html`;
      link.target = "_blank";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      toast.success("Resume opened for download");
      return;
    }
    setDownloadLoading(true);
    try {
      const { data } = await getResumeDownload(candidateId);
      const link = document.createElement("a");
      link.href = data.url;
      link.download = data.filename || "resume.pdf";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      toast.success("Resume downloaded successfully!");
    } catch (err) {
      console.error("Failed to download resume:", err);
      toast.error("Failed to download resume");
    } finally {
      setDownloadLoading(false);
    }
  };

  if (!isOpen) return null;

  const canPreview =
    Boolean(mockResumeUrl) || resumeStatus === "completed";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-4xl rounded-3xl border border-slate-200 bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        style={{ maxHeight: "90vh" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
          <div>
            <h2 className="text-xl font-semibold text-slate-900">
              Resume - {candidateName}
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Review candidate resume
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleDownloadResume}
              disabled={downloadLoading || loading || !!error || !canPreview}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {downloadLoading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Downloading...
                </>
              ) : (
                <>
                  <Download className="h-4 w-4" />
                  Download
                </>
              )}
            </button>
            <button
              onClick={onClose}
              className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="overflow-auto" style={{ maxHeight: "calc(90vh - 80px)" }}>
          {loading ? (
            <div className="flex min-h-[400px] flex-col items-center justify-center gap-3 text-slate-500">
              <Loader2 className="h-8 w-8 animate-spin text-blue-500" />
              <p>Loading resume...</p>
            </div>
          ) : error ? (
            <div className="flex min-h-[400px] flex-col items-center justify-center gap-3">
              <AlertCircle className="h-8 w-8 text-red-400" />
              <div className="text-center">
                <p className="text-sm font-medium text-slate-900">{error}</p>
                <button
                  onClick={fetchResumePreview}
                  className="mt-3 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700 hover:bg-red-100 transition"
                >
                  Retry
                </button>
              </div>
            </div>
          ) : !canPreview ? (
            <div className="flex min-h-[400px] flex-col items-center justify-center gap-3">
              <AlertCircle className="h-8 w-8 text-yellow-400" />
              <div className="text-center">
                <p className="text-sm font-medium text-slate-900">
                  Resume not available
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  Status: {resumeStatus}
                </p>
              </div>
            </div>
          ) : resumeUrl ? (
            <iframe
              src={resumeUrl}
              className="h-full w-full bg-white"
              style={{ minHeight: "500px" }}
              title="Resume Preview"
            />
          ) : null}
        </div>
      </div>
    </div>
  );
};

export default ResumeModal;
