import React from "react";

interface ErrorBoundaryState {
    hasError: boolean;
}

export class ErrorBoundary extends React.Component<
    { children: React.ReactNode },
    ErrorBoundaryState
> {
    constructor(props: { children: React.ReactNode }) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError(): ErrorBoundaryState {
        return { hasError: true };
    }

    componentDidCatch(error: Error, info: React.ErrorInfo) {
        if (import.meta.env.DEV) {
            console.warn("ErrorBoundary caught:", error.message, info.componentStack);
        }
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className="flex flex-col items-center justify-center h-screen gap-4 p-8 text-center">
                    <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">
                        Something went wrong
                    </h1>
                    <p className="text-zinc-500 dark:text-zinc-400 max-w-md">
                        An unexpected error occurred. Please refresh the page to try again.
                    </p>
                    <button
                        onClick={() => {
                            this.setState({ hasError: false });
                            window.location.href = "/";
                        }}
                        className="px-4 py-2 bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900 rounded-md hover:opacity-90 transition-opacity"
                    >
                        Refresh
                    </button>
                </div>
            );
        }

        return this.props.children;
    }
}
