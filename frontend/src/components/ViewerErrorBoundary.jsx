import { Component } from "react";

// ✅ NEW: React requires a class component for error boundaries (no hook
// equivalent exists). This wraps just the PDF preview so that if pdf.js
// throws on a given file, the toolbar/download button above it stays alive
// instead of the whole app unmounting to a blank white page.
export default class ViewerErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error("PDF viewer crashed:", error, info);
  }

  componentDidUpdate(prevProps) {
    // reset the boundary whenever the underlying doc changes, so a fresh
    // upload/edit gets a clean retry instead of staying stuck on the error
    if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false, error: null });
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="pdf-error">
          Couldn't display the PDF preview ({this.state.error?.message || "unknown error"}).
          The edit itself still went through on the server — use "Generate / Download PDF"
          above to get the file with your changes.
        </div>
      );
    }
    return this.props.children;
  }
}