// Faithful implementation of the Figma "aero-intel-desktop" welcome screen
// (node 70:4), pulled via get_design_context — real icon assets, exact
// colors/fonts/radii, not an approximation. See plan Section 5a.

import planeIcon from "../assets/plane.svg";
import barChart3Icon from "../assets/bar-chart-3.svg";
import briefcaseIcon from "../assets/briefcase.svg";
import usersIcon from "../assets/users.svg";

interface SuggestionPrompt {
  icon: string;
  text: string;
}

const SUGGESTIONS: SuggestionPrompt[] = [
  { icon: barChart3Icon, text: "Which airports in New England are strong candidates for terminal expansion?" },
  { icon: briefcaseIcon, text: "Compare congestion at LAX and SNA" },
  { icon: usersIcon, text: "What percentage of flights from Anchorage are long-haul?" },
];

interface WelcomeScreenProps {
  onSelectSuggestion: (text: string) => void;
  showBrand?: boolean;
}

export function WelcomeScreen({ onSelectSuggestion, showBrand = true }: WelcomeScreenProps) {
  return (
    <>
      {showBrand && <div className="bg-glow" aria-hidden="true" />}

      {showBrand && (
        <div className="brand">
          <div className="brand-mark">
            <img src={planeIcon} alt="" width={20} height={20} />
          </div>
          <div className="brand-copy">
            <p className="brand-name">Aero Intel</p>
            <p className="brand-tagline">Airport investment intelligence</p>
          </div>
        </div>
      )}

      <div className="welcome-area">
        <p className="welcome-headline">
          Ask Aero Intel about airport demand, yields, and infrastructure moves.
        </p>
        <p className="welcome-subtext">
          Start with a prompt, attach a report, or continue a recent airport analysis.
        </p>
      </div>

      <div className="suggestions">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.text}
            className="suggestion-card"
            onClick={() => onSelectSuggestion(s.text)}
          >
            <span className="suggestion-icon">
              <img src={s.icon} alt="" width={18} height={18} />
            </span>
            <span>{s.text}</span>
          </button>
        ))}
      </div>
    </>
  );
}
