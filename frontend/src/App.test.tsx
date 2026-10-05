import { render, screen } from '@testing-library/react';
import App from './App';

test('renders the search box', () => {
  render(<App />);
  expect(screen.getByRole('combobox')).toBeInTheDocument();
  expect(screen.getByText(/Enter a ticker/i)).toBeInTheDocument();
});
