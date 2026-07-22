import markImage from "../assets/talentfloww-mark.png";

/**
 * talentFloww logo — document + flowing arrow on a white background.
 */
const Logo = ({
  className = "h-10 w-10",
  title = "talentFloww logo",
  variant = "mark",
}) => {
  if (variant === "wordmark") {
    return (
      <div
        className={`flex min-w-0 items-center gap-2.5 ${className}`}
        title={title}
      >
        <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-white">
          <img
            src={markImage}
            alt=""
            className="h-full w-full bg-white object-contain"
          />
        </span>
        <div className="min-w-0 text-left leading-tight">
          <p className="whitespace-nowrap text-[15px] font-bold tracking-tight text-[#14344a]">
            talent<span className="text-[#2eb8c9]">Floww</span>
          </p>
          <p className="whitespace-nowrap text-[10px] font-medium text-slate-500">
            Recruitment Intelligence AI
          </p>
        </div>
      </div>
    );
  }

  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center overflow-hidden rounded-lg bg-white ${className}`}
    >
      <img
        src={markImage}
        alt={title}
        className="h-full w-full bg-white object-contain"
      />
    </span>
  );
};

export default Logo;
