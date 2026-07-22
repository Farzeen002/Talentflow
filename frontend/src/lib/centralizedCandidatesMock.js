/**
 * Mock teammates + candidates for Centralized Candidates.
 * Replace with API when cross-recruiter feed exists.
 */

export const MOCK_RECRUITERS = ["Vineeth", "Prapthi", "Nischal", "Nasiha"];

const RESUME_FILES = [
  "/mock-resumes/priya-menon.html",
  "/mock-resumes/rohan-kapoor.html",
  "/mock-resumes/ananya-iyer.html",
  "/mock-resumes/vikram-shah.html",
  "/mock-resumes/sneha-reddy.html",
  "/mock-resumes/arjun-nair.html",
  "/mock-resumes/meera-joshi.html",
  "/mock-resumes/karthik-rao.html",
];

const FIRST_NAMES = [
  "Priya", "Rohan", "Ananya", "Vikram", "Sneha", "Arjun", "Meera", "Karthik",
  "Divya", "Imran", "Neha", "Siddharth", "Aisha", "Rahul", "Kavya", "Aditya",
  "Pooja", "Nikhil", "Shreya", "Varun", "Isha", "Manish", "Tanvi", "Harsh",
  "Ritu", "Aman", "Deepa", "Suresh", "Lakshmi", "Kiran", "Swati", "Pranav",
  "Gayatri", "Mohit", "Nandini", "Rajesh", "Sonal", "Vivek", "Anjali", "Farhan",
  "Trisha", "Gaurav", "Bhavya", "Yash", "Malavika", "Omkar", "Rashmi", "Dev",
  "Keerthana", "Sagar",
];

const LAST_NAMES = [
  "Menon", "Kapoor", "Iyer", "Shah", "Reddy", "Nair", "Joshi", "Rao",
  "Patil", "Ali", "Sharma", "Gupta", "Khan", "Verma", "Pillai", "Das",
  "Chopra", "Bhat", "Mehta", "Singh", "Nambiar", "Deshmukh", "Banerjee", "Shetty",
  "Kulkarni", "Agarwal", "Thomas", "Fernandes", "Mukherjee", "Saxena",
];

const ROLES = [
  ["Senior Java Backend Engineer", "Infosys", ["Java", "Spring Boot", "Kafka", "PostgreSQL"]],
  ["QA Automation Lead", "Deloitte", ["Playwright", "Selenium", "Java", "Jenkins"]],
  ["Frontend Engineer", "Freshworks", ["React", "TypeScript", "Tailwind", "Vite"]],
  ["DevOps Engineer", "Persistent Systems", ["Kubernetes", "Terraform", "AWS", "Prometheus"]],
  ["Data Engineer", "Accenture", ["Python", "Spark", "Airflow", "BigQuery"]],
  ["Full Stack Developer", "UST Global", ["React", "Node.js", "MongoDB", "Redis"]],
  ["Product Designer", "Razorpay", ["Figma", "Design Systems", "User Research"]],
  [".NET Developer", "LTTS", ["C#", ".NET Core", "Azure", "SQL Server"]],
  ["Business Analyst", "TCS", ["Jira", "Confluence", "SQL", "Stakeholder Mgmt"]],
  ["Android Developer", "PhonePe", ["Kotlin", "Jetpack", "MVVM", "Firebase"]],
  ["iOS Developer", "Swiggy", ["Swift", "SwiftUI", "Combine", "CoreData"]],
  ["SRE", "Google", ["SRE", "GCP", "Go", "Observability"]],
  ["ML Engineer", "Amazon", ["Python", "PyTorch", "MLOps", "SageMaker"]],
  ["Security Engineer", "Wipro", ["AppSec", "OWASP", "Python", "SIEM"]],
  ["Salesforce Developer", "Capgemini", ["Apex", "LWC", "SOQL", "Integration"]],
  ["Tech Lead", "Cognizant", ["Architecture", "Java", "Mentoring", "Agile"]],
];

const LOCATIONS = [
  "Bengaluru", "Hyderabad", "Chennai", "Pune", "Mumbai", "Kochi",
  "Delhi NCR", "Ahmedabad", "Jaipur", "Coimbatore",
];

const JOB_PREFIXES = [
  "JAVA", "QA", "FE", "DEVOPS", "DATA", "FS", "UX", "DOTNET",
  "BA", "AND", "IOS", "SRE", "ML", "SEC", "SF", "TL",
];

/** Recruiter chip colors (stable by name) */
export const RECRUITER_CHIP = {
  Vineeth: "bg-sky-100 text-sky-800 border-sky-200",
  Prapthi: "bg-violet-100 text-violet-800 border-violet-200",
  Nischal: "bg-amber-100 text-amber-900 border-amber-200",
  Nasiha: "bg-rose-100 text-rose-800 border-rose-200",
  You: "bg-emerald-100 text-emerald-800 border-emerald-200",
};

export function recruiterChipClass(name) {
  if (RECRUITER_CHIP[name]) return RECRUITER_CHIP[name];
  return "bg-slate-100 text-slate-700 border-slate-200";
}

function buildMockCandidate(index) {
  const i = index; // 0..49
  const first = FIRST_NAMES[i % FIRST_NAMES.length];
  const last = LAST_NAMES[(i * 3) % LAST_NAMES.length];
  const role = ROLES[i % ROLES.length];
  const recruiter = MOCK_RECRUITERS[i % MOCK_RECRUITERS.length];
  const day = 1 + (i % 18);
  const exp = Number((2.5 + (i % 10) * 0.7).toFixed(1));
  const currentCtc = 10 + (i % 16);
  const expectedCtc = currentCtc + 3 + (i % 5);
  const notice = [15, 30, 45, 60, 90][i % 5];
  const jobCode = `${JOB_PREFIXES[i % JOB_PREFIXES.length]}${String(100 + i).padStart(3, "0")}`;
  const id = `mock-cand-${String(i + 1).padStart(2, "0")}`;

  return {
    candidateId: id,
    name: `${first} ${last}`,
    currentRole: role[0],
    currentCompany: role[1],
    experienceYears: exp,
    noticePeriodDays: notice,
    currentCtc,
    expectedCtc,
    resumeStatus: "completed",
    atsScore: 68 + (i % 30),
    recruiterName: recruiter,
    isMine: false,
    isMock: true,
    jobId: jobCode,
    jobTitle: role[0],
    createdAt: `2026-07-${String(day).padStart(2, "0")}T${String(8 + (i % 10)).padStart(2, "0")}:${String((i * 7) % 60).padStart(2, "0")}:00+05:30`,
    resumeUrl: RESUME_FILES[i % RESUME_FILES.length],
    currentLocation: LOCATIONS[i % LOCATIONS.length],
    skills: role[2],
    email: `${first.toLowerCase()}.${last.toLowerCase()}@email.com`,
    phone: `+91 9${String(800000000 + i * 137).slice(0, 9)}`,
    qa: {
      isOkClient: i % 5 !== 0,
      isC2hOk: i % 4 !== 0,
      hasPfAccount: i % 3 !== 0,
      willingToRelocate: i % 2 === 0,
      noticePeriodDays: notice,
      currentCtc,
      expectedCtc,
      experienceRunManagementYears: i % 6 === 0 ? 2 : null,
      experienceServiceDeliveryYears: i % 7 === 0 ? 3 : null,
    },
  };
}

/** 50 mock team candidates for All tab */
export const MOCK_TEAM_CANDIDATES = Array.from({ length: 50 }, (_, i) =>
  buildMockCandidate(i)
);

export const MOCK_CANDIDATE_COUNT = MOCK_TEAM_CANDIDATES.length;

export function getMockCandidateById(candidateId) {
  return MOCK_TEAM_CANDIDATES.find((c) => c.candidateId === candidateId) ?? null;
}

/**
 * Shape compatible with CandidateDetails page sections.
 */
export function mockToDetailShape(c) {
  if (!c) return null;
  const filename = `${c.name.replace(/\s+/g, "_").toUpperCase()}.pdf`;
  return {
    candidateId: c.candidateId,
    jobId: c.jobId,
    createdAt: c.createdAt,
    updatedAt: c.createdAt,
    metadata: {
      name: c.name,
      currentRole: c.currentRole,
      currentCompany: c.currentCompany,
      jobTitle: c.jobTitle,
      experienceYears: c.experienceYears,
      currentLocation: c.currentLocation,
      profileNoticeDays: c.noticePeriodDays,
      profileCtcRupees: c.currentCtc != null ? c.currentCtc * 100000 : null,
    },
    qa: {
      ...(c.qa ?? {}),
      noticePeriodDays: c.noticePeriodDays,
      currentCtc: c.currentCtc,
      expectedCtc: c.expectedCtc,
    },
    skills: { raw: c.skills ?? [] },
    resume: {
      status: "completed",
      original: {
        filename,
        sizeBytes: 180000 + ((c.candidateId?.length || 1) * 1373) % 90000,
        contentType: "application/pdf",
      },
    },
    processing: { needsReview: false },
    email: {
      from: c.email,
      subject: `Application — ${c.jobTitle}`,
      timestamp: c.createdAt,
    },
    blacklist: { isBlacklisted: false },
    atsScore: c.atsScore,
    recruiterName: c.recruiterName,
    isMock: true,
    resumeUrl: c.resumeUrl,
    phone: c.phone,
  };
}

/** HTML resume that matches this candidate (for iframe preview). */
export function buildMockResumeHtml(c) {
  const skills = (c.skills ?? []).join(" · ");
  const company = c.currentCompany || "Previous Employer";
  const role = c.currentRole || "Professional";
  const exp = c.experienceYears ?? "—";
  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/><title>Resume — ${c.name}</title>
<style>
  body{font-family:Arial,Helvetica,sans-serif;max-width:720px;margin:36px auto;padding:0 28px;color:#1e293b;line-height:1.5}
  h1{margin:0;font-size:26px;color:#0f172a;letter-spacing:-0.02em}
  .meta{font-size:13px;color:#64748b;margin-top:6px}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:0.08em;color:#14344a;border-bottom:1px solid #cbd5e1;padding-bottom:4px;margin-top:26px}
  ul{padding-left:18px;margin:8px 0}
</style></head><body>
  <h1>${c.name}</h1>
  <p class="meta">${role} · ${c.currentLocation || ""} · ${c.email || ""} · ${c.phone || ""} · ${exp} years</p>
  <h2>Summary</h2>
  <p>Experienced ${role} with ${exp} years in the industry, currently at ${company}. Strong track record delivering production systems and collaborating with cross-functional hiring partners.</p>
  <h2>Experience</h2>
  <p><strong>${company}</strong> — ${role} (Recent)</p>
  <ul>
    <li>Owned delivery for key product initiatives aligned to ${c.jobTitle || role}.</li>
    <li>Partnered with stakeholders to improve quality, throughput, and documentation.</li>
  </ul>
  <p><strong>Earlier role</strong> — Individual Contributor</p>
  <ul>
    <li>Built foundational skills across ${skills || "core technologies"}.</li>
  </ul>
  <h2>Skills</h2>
  <p>${skills || "—"}</p>
  <h2>Education</h2>
  <p>B.Tech / B.E. — Relevant engineering degree</p>
</body></html>`;
}

/**
 * Normalize a real CandidateSummary from listCandidates into CentralCandidate.
 */
export function mapMyCandidate(summary, { recruiterName, jobId, jobTitle } = {}) {
  return {
    candidateId: summary.candidateId,
    name: summary.name || "Unnamed",
    currentRole: summary.currentRole || summary.jobTitle || "—",
    currentCompany: summary.currentCompany || "—",
    experienceYears: summary.experienceYears,
    noticePeriodDays: summary.noticePeriodDays,
    currentCtc: summary.currentCtc,
    expectedCtc: summary.expectedCtc,
    resumeStatus: summary.resumeStatus || "missing",
    atsScore: summary.atsScore,
    recruiterName: recruiterName || "You",
    isMine: true,
    isMock: false,
    jobId: jobId || summary.jobId,
    jobTitle: jobTitle || summary.jobTitle,
    createdAt: summary.createdAt,
    currentLocation: summary.currentLocation,
    skills: summary.skills?.raw || summary.skills || [],
  };
}
