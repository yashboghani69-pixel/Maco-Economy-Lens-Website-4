import { useEffect } from "react";
import "@/App.css";

function App() {
  useEffect(() => {
    window.location.replace("/macro.html");
  }, []);
  return (
    <div data-testid="app-redirect" style={{
      minHeight: "100vh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      background: "#F7F5F0",
      color: "#1C1C1A",
      fontFamily: "system-ui, sans-serif",
      fontSize: 14
    }}>
      Opening India Macro Lens…
    </div>
  );
}

export default App;
