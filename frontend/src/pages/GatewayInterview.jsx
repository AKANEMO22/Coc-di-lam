import { useNavigate, useParams } from "react-router-dom";
import { SessionPage } from "@interview/components/SessionPage";
import "virtual:interview.css";
import "../gateway.css";

export default function GatewayInterview() {
  const navigate = useNavigate();
  const { sessionId } = useParams();

  if (!sessionId) return null;
  return <div className="gateway-ui gateway-screen"><SessionPage key={sessionId} sessionId={sessionId} onBack={() => navigate("/results")} /></div>;
}
