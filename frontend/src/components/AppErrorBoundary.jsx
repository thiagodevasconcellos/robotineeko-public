import { Component } from 'react'

export class AppErrorBoundary extends Component {
    constructor(props) {
        super(props)
        this.state = {
            hasError: false,
            errorMessage: '',
        }
    }

    static getDerivedStateFromError(error) {
        return {
            hasError: true,
            errorMessage: String(error?.message || 'Unknown application error.'),
        }
    }

    componentDidCatch(error, errorInfo) {
        try {
            console.error('Robotineeko UI crashed:', error, errorInfo)
        } catch {
            // Ignore console failures in degraded browser environments.
        }
    }

    handleReload = () => {
        window.location.reload()
    }

    render() {
        if (!this.state.hasError) {
            return this.props.children
        }

        return (
            <div className='appErrorBoundaryShell'>
                <div className='appErrorBoundaryCard'>
                    <div className='appErrorBoundaryEyebrow'>Interface recovery</div>
                    <h1>Algo quebrou na interface</h1>
                    <p>
                        O backend pode continuar saudável, mas a tela encontrou um erro de renderizacao.
                        Voce pode recarregar a pagina sem perder o estado salvo no backend.
                    </p>
                    <code>{this.state.errorMessage}</code>
                    <button type='button' onClick={this.handleReload}>
                        Recarregar interface
                    </button>
                </div>
            </div>
        )
    }
}
